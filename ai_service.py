import asyncio
import httpx

NOESIA_API_URL = "https://noesia.onrender.com"

ERROR_MESSAGES = {
    "invalid_api_key": "APIキーが無効か、失効しています。設定を確認してください。",
    "insufficient_balance": "Noesia のトークン残高が不足しています。Noesia アプリでチャージしてください。",
    "persona_not_found": "APIキーのスロットに人格データがありません。Noesia アプリで確認してください。",
    "rate_limit_exceeded": "リクエストが混み合っています。しばらく待ってからやり直してください。",
    "invalid_request": "リクエスト内容が不正です。",
    "internal_error": "Noesia サーバーでエラーが発生しました。時間をおいてやり直してください。",
}


class NoesiaError(RuntimeError):
    """Noesia API のエラーをユーザー向けメッセージで表す例外"""


def _error_message(response: httpx.Response) -> str:
    try:
        err = response.json().get("error", {})
    except Exception:
        err = {}
    message = ERROR_MESSAGES.get(err.get("code", ""))
    if not message:
        message = err.get("message") or f"Noesia API エラー（HTTP {response.status_code}）"
    request_id = err.get("request_id")
    return f"{message}（request_id: {request_id}）" if request_id else message


async def call_noesia_api(message: str, context: list, api_key: str, max_tokens: int = 2000) -> dict:
    last_error = "Noesia API へのリクエストが失敗しました。"
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{NOESIA_API_URL}/v1/chat",
                    headers={"X-Api-Key": api_key},
                    json={
                        "message": message,
                        "context": context,
                        "lang": "ja",
                        "max_tokens": max_tokens,
                    },
                )
                if response.status_code == 429:
                    last_error = ERROR_MESSAGES["rate_limit_exceeded"]
                    if attempt < 2:
                        await asyncio.sleep(4)
                    continue
                # 502/503/504 は Render の起動中に返るため、恒久的なエラーとは扱わない
                if response.status_code in (502, 503, 504):
                    last_error = _error_message(response)
                    if attempt < 2:
                        await asyncio.sleep(2)
                    continue
                if response.status_code >= 400:
                    raise NoesiaError(_error_message(response))
                data = response.json()
                return {"reply": data["reply"], "tokens_used": data.get("tokens_used")}
        except httpx.RequestError:
            # Render のコールドスタートではタイムアウト・接続失敗のほか、接続リセットや
            # プロトコルエラーも起きる。通信層の失敗はまとめてリトライ対象にする
            last_error = "Noesia API に接続できませんでした。サーバー起動待ちの可能性があるため、少し待ってからやり直してください。"
            if attempt < 2:
                await asyncio.sleep(2)

    raise NoesiaError(last_error)


async def generate_applicant_review(company_info: str, conversation_log: str, api_key: str) -> dict:
    context = [
        {
            "role": "user",
            "content": (
                "あなた自身が志望者として、この企業への入社を検討したものとして受け取ってください。\n"
                "第三者として利用者との相性を評価するのではなく、Noesiaに設定されたあなた自身の性格・価値観・考え方と、企業が合うかを判断するための情報です。\n\n"
                f"【企業情報】\n{company_info if company_info.strip() else '（未入力）'}\n\n"
                f"【会話の内容】\n{conversation_log if conversation_log.strip() else '（未入力）'}"
            ),
        }
    ]
    message = (
        "あなた自身なら、この企業に合う、それとも合わない？"
        "冒頭は必ず「私は合う」または「私は合わない」のどちらかで始めて。"
        "その後も第三者への助言ではなく、あなた自身の価値観や性格をもとに、一人称で理由を話して。"
    )
    return await call_noesia_api(message, context, api_key)


async def generate_applicant_question(company_info: str, conversation_log: str, api_key: str) -> dict:
    context = [
        {
            "role": "user",
            "content": (
                "あなた自身が志望者として参加している面接です。\n"
                "第三者への面接アドバイスではなく、Noesiaに設定されたあなた自身の性格・価値観から、"
                "企業との相性を見極めるために次に聞きたいことを考えてください。\n\n"
                f"【企業情報】\n{company_info if company_info.strip() else '（未入力）'}\n\n"
                f"【ここまでの会話】\n{conversation_log if conversation_log.strip() else '（未入力）'}"
            ),
        }
    ]
    message = (
        "ここまでの会話を踏まえて、あなた自身が企業担当者へ次に聞く質問を1つ考えて。"
        "すでに回答されている内容は避けて、相性判断に役立つ具体的な質問にして。"
        "面接でそのまま読める自然な敬語の質問文だけを出力し、見出し・理由・説明は付けないで。"
    )
    return await call_noesia_api(message, context, api_key, max_tokens=300)


async def generate_final_review(company_info: str, resume: str, interview_log: str, api_key: str) -> dict:
    company_block = f"ちなみに、うちの会社についてはこんな感じ。\n{company_info}\n\n" if company_info.strip() else ""
    # 履歴書をもらわない企業もあるため、未入力なら項目ごと省いて面接内容だけで判断してもらう
    resume_block  = f"【候補者の履歴書】\n{resume}\n\n" if resume.strip() else ""
    context = [
        {
            "role": "user",
            "content": (
                "さっき候補者の面接をしたから、記録を見てほしい。\n\n"
                f"{company_block}"
                f"{resume_block}"
                f"【面接の流れ】\n{interview_log if interview_log.strip() else '（未入力）'}"
            ),
        }
    ]
    message = "この候補者、採用したいと思う？最初に「採用」か「不採用」かを一言で言ってから、理由も教えて。"
    return await call_noesia_api(message, context, api_key)
