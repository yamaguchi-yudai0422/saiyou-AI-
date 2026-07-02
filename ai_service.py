import asyncio
import httpx

NOESIA_API_URL = "https://noesia.onrender.com"


async def call_noesia_api(message: str, context: list, api_key: str) -> str:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{NOESIA_API_URL}/v1/chat",
                    headers={"X-Api-Key": api_key},
                    json={
                        "message": message,
                        "context": context,
                        "lang": "ja",
                        "max_tokens": 600,
                    },
                )
                if response.status_code == 429:
                    await asyncio.sleep(4)
                    continue
                response.raise_for_status()
                return response.json()["reply"]
        except httpx.TimeoutException:
            if attempt == 2:
                raise
            await asyncio.sleep(2)

    raise RuntimeError("Noesia API へのリクエストが失敗しました。")


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
    message = "この企業は私に合っていると思う？合わない？どっちかで答えて、理由も教えて。"
    reply = await call_noesia_api(message, context, api_key)
    return {"reply": reply}


async def generate_final_review(company_info: str, resume: str, interview_log: str, api_key: str) -> dict:
    company_block = f"ちなみに、うちの会社についてだけど。\n{company_info}\n\n" if company_info.strip() else ""
    context = [
        {
            "role": "user",
            "content": (
                "さっきの面接、全部見てたよね。\n\n"
                f"{company_block}"
                f"【候補者の履歴書】\n{resume if resume.strip() else '（未入力）'}\n\n"
                f"【面接の流れ】\n{interview_log}"
            ),
        }
    ]
    message = "この候補者、採用したい？不採用？どっちかで答えて、理由も教えて。"

    reply = await call_noesia_api(message, context, api_key)
    return {"reply": reply}
