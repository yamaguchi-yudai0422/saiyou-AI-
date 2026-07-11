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


async def call_noesia_api(message: str, context: list, api_key: str, max_tokens: int = 1000) -> dict:
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
                    await asyncio.sleep(4)
                    continue
                if response.status_code >= 400:
                    raise NoesiaError(_error_message(response))
                data = response.json()
                return {"reply": data["reply"], "tokens_used": data.get("tokens_used")}
        except (httpx.TimeoutException, httpx.ConnectError):
            # Render のコールドスタートでタイムアウト・接続失敗が起きやすいためリトライする
            last_error = "Noesia API に接続できませんでした。サーバー起動待ちの可能性があるため、少し待ってからやり直してください。"
            if attempt < 2:
                await asyncio.sleep(2)

    raise NoesiaError(last_error)


async def generate_applicant_review(company_info: str, conversation_log: str, api_key: str) -> dict:
    context = [
        {
            "role": "user",
            "content": (
                "私はある企業への入社を検討していて、企業の方と話しました。\n\n"
                f"【企業情報】\n{company_info if company_info.strip() else '（未入力）'}\n\n"
                f"【会話の内容】\n{conversation_log if conversation_log.strip() else '（未入力）'}"
            ),
        }
    ]
    message = "この企業、私に合ってると思う？最初に「合う」か「合わない」かを一言で言ってから、理由も教えて。"
    return await call_noesia_api(message, context, api_key)


async def generate_final_review(company_info: str, resume: str, interview_log: str, api_key: str) -> dict:
    company_block = f"ちなみに、うちの会社についてはこんな感じ。\n{company_info}\n\n" if company_info.strip() else ""
    context = [
        {
            "role": "user",
            "content": (
                "さっき候補者の面接をしたから、記録を見てほしい。\n\n"
                f"{company_block}"
                f"【候補者の履歴書】\n{resume if resume.strip() else '（未入力）'}\n\n"
                f"【面接の流れ】\n{interview_log if interview_log.strip() else '（未入力）'}"
            ),
        }
    ]
    message = "この候補者、採用したいと思う？最初に「採用」か「不採用」かを一言で言ってから、理由も教えて。"
    return await call_noesia_api(message, context, api_key)
