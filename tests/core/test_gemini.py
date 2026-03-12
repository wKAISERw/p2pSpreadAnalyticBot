# test_gemini.py
import asyncio
import json
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

SYSTEM_PROMPT = """Ти аналізатор ризиків для P2P.
Поверни тільки JSON без пояснень.
"""

USER_PROMPT = """Поверни рівно такий JSON:
{"status":"OK","risk":"NONE","reason":"тест gemini"}
"""

async def main():
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не встановлений")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    payload = {
        "contents": [{
            "parts": [{
                "text": f"{SYSTEM_PROMPT}\n\n{USER_PROMPT}"
            }]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 120,
            "responseMimeType": "application/json",
        },
    }

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            text = await resp.text()

            print("=" * 80)
            print("MODEL:", GEMINI_MODEL)
            print("HTTP STATUS:", resp.status)
            print("RAW RESPONSE:")
            print(text)
            print("=" * 80)

            if resp.status != 200:
                return

            data = json.loads(text)
            candidate_text = data["candidates"][0]["content"]["parts"][0]["text"]

            print("PARSED TEXT:")
            print(candidate_text)
            print("=" * 80)

            try:
                parsed = json.loads(candidate_text)
                print("JSON OK:")
                print(json.dumps(parsed, ensure_ascii=False, indent=2))
            except json.JSONDecodeError as e:
                print("JSON PARSE ERROR:", e)

if __name__ == "__main__":
    asyncio.run(main())
