# core/llm_worker.py
"""
Асинхронний LLM-воркер (2 паралельних воркери).

Pipeline:
  scanner → pending_set + asyncio.Queue → llm_worker → MerchantDB
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from core.merchant_db import MerchantDB, hash_terms
from core.regex_analyzer import RegexResult

logger = logging.getLogger("LLMWorker")

# ── LLM лог ──────────────────────────────────────────────────────────────────
def _setup_llm_log() -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    llm_log = logging.getLogger("LLMDecisions")
    if not llm_log.handlers:
        h = RotatingFileHandler("logs/llm_decisions.log",
                                maxBytes=5 * 1024 * 1024, backupCount=3,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        llm_log.addHandler(h)
        llm_log.setLevel(logging.INFO)
        llm_log.propagate = False
    return llm_log

LLM_LOG = _setup_llm_log()

LLM_WORKERS  = 1
LLM_TIMEOUT  = 7.0
MAX_QUEUE    = 200

# ── Системний промпт ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ти — антифрод-система для P2P криптообміну UAH/USDT на ринку України.
Аналізуй умови угоди мерчанта та визнач ризик шахрайства.

СХЕМИ ДЛЯ БЛОКУВАННЯ:
- TRIANGLE: просить переказ від третьої особи, згадує дропів, чужі картки, "без коментарів у призначенні", "переказ від знайомого"
- CASINO: букмекери (1xbet, melbet, mostbet), казино, ставки, процесинг, агрегатор
- CHAT_FIRST: "пишіть перед оплатою", "напишіть в тг спочатку", контакт до угоди
- SUSPICIOUS: анонімно, без перевірки, ФОП оплата, погроза апеляцією

КОНТЕКСТ УКРАЇНСЬКОГО P2P:
- "дроп" = людина що дає картку шахраям → TRIANGLE
- "без коментарів/призначення" = ознака трикутника → TRIANGLE  
- "тільки Mono/Privat/ПУМБ" = нормально, не ризик
- "переказ від знайомого/друга" = трикутник → TRIANGLE
- Короткі нейтральні умови = нормально

ВІДПОВІДАЙ ВИКЛЮЧНО JSON (без markdown, без тексту навколо):
{"status":"OK"|"SUSPICIOUS"|"BLOCK","risk":"TRIANGLE"|"CASINO"|"CHAT_FIRST"|"SUSPICIOUS"|"NONE","reason":"до 100 символів українською"}"""


# ── Task dataclass ────────────────────────────────────────────────────────────
@dataclass
class LLMTask:
    exchange:      str
    merchant_id:   str
    merchant_name: str
    trade_terms:   str
    regex_result:  RegexResult


# ── LLMWorkerPool ─────────────────────────────────────────────────────────────
class LLMWorkerPool:
    def __init__(self, db: MerchantDB):
        self._db               = db
        self._queue: asyncio.Queue[LLMTask] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._pending: set[tuple[str, str]] = set()
        self._workers: list[asyncio.Task]   = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats = {"processed": 0, "blocks": 0, "timeouts": 0, "errors": 0}

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT + 1.5),
            headers={"Content-Type": "application/json"},
        )
        for i in range(LLM_WORKERS):
            t = asyncio.create_task(self._worker(i), name=f"llm-worker-{i}")
            self._workers.append(t)
        logger.info("LLMWorkerPool запущено (%d воркерів)", LLM_WORKERS)

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        if self._session:
            await self._session.close()
        s = self._stats
        logger.info("LLMWorkerPool зупинено: оброблено=%d блоків=%d таймаутів=%d помилок=%d",
                    s["processed"], s["blocks"], s["timeouts"], s["errors"])

    def schedule(self, exchange: str, merchant_id: str, merchant_name: str,
                 trade_terms: str, regex_result: RegexResult) -> bool:
        """Неблокуючий — scanner викликає і забуває."""
        key = (exchange, merchant_id)
        if key in self._pending:
            return False

        task = LLMTask(exchange, merchant_id, merchant_name,
                       trade_terms, regex_result)
        try:
            self._queue.put_nowait(task)
            self._pending.add(key)
            logger.debug("В чергу LLM: %s [%s]", merchant_name, exchange)
            return True
        except asyncio.QueueFull:
            logger.debug("LLM черга переповнена, пропускаємо %s", merchant_name)
            return False

    async def _worker(self, worker_id: int) -> None:
        while True:
            try:
                task = await self._queue.get()
                try:
                    await self._process(task)
                finally:
                    self._pending.discard((task.exchange, task.merchant_id))
                    self._queue.task_done()

                # 🛡 ЗАХИСТ ВІД БАНУ GROQ (Не більше 28 запитів на хвилину)
                await asyncio.sleep(2.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["errors"] += 1
                logger.error("LLM Worker %d помилка: %s", worker_id, e, exc_info=True)
                await asyncio.sleep(1.0)


    async def _process(self, task: LLMTask) -> None:
        cached = await self._db.get_verdict(
            task.exchange, task.merchant_id, task.trade_terms
        )
        if cached and cached not in ("UNKNOWN", "NEEDS_LLM"):
            return

        result = await self._call_with_fallback(task)
        self._stats["processed"] += 1

        verdict   = result.get("status", "UNKNOWN").upper()
        risk_type = result.get("risk", "")
        reason    = result.get("reason", "")
        source    = result.get("_source", "unknown")

        if verdict == "BLOCK":
            self._stats["blocks"] += 1

        await self._db.save_verdict(
            task.exchange, task.merchant_id, task.merchant_name,
            task.trade_terms, verdict, risk_type, reason, source
        )

        LLM_LOG.info(
            "%-8s | %-10s | %-6s | %-12s | %-8s | %s | %s",
            task.exchange, task.merchant_id[:10], verdict,
            risk_type or "NONE", source,
            task.merchant_name[:20],
            reason[:80]
        )

        if verdict == "BLOCK":
            logger.warning("🚫 LLM BLOCK [%s] %s [%s]: %s — %s",
                           source, task.merchant_name, task.exchange,
                           risk_type, reason)

    async def _call_with_fallback(self, task: LLMTask) -> dict:
        user_msg = _build_prompt(task)

        # Groq спроба
        try:
            result = await asyncio.wait_for(
                self._call_groq(user_msg), timeout=LLM_TIMEOUT
            )
            result["_source"] = "groq"
            return result
        except asyncio.TimeoutError:
            self._stats["timeouts"] += 1
            logger.warning("⏳ Groq timeout для %s", task.merchant_name)
        except Exception as e:
            logger.error("❌ Groq помилка: %s", e)

        # Gemini fallback
        try:
            result = await asyncio.wait_for(
                self._call_gemini(user_msg), timeout=LLM_TIMEOUT
            )
            result["_source"] = "gemini"
            return result
        except asyncio.TimeoutError:
            self._stats["timeouts"] += 1
            logger.warning("⏳ Gemini timeout для %s", task.merchant_name)
        except Exception as e:
            logger.error("❌ Gemini помилка: %s", e)

        return {"status": "UNKNOWN", "risk": "", "reason": "LLM не відповіла", "_source": "timeout"}

    async def _call_groq(self, user_msg: str) -> dict:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY не встановлений")

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens":  120,
            "response_format": {"type": "json_object"},
        }
        async with self._session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as resp:
            if resp.status == 429:
                raise RuntimeError("Groq rate limit 429")
            if resp.status != 200:
                raise RuntimeError(f"Groq HTTP {resp.status}")
            data = await resp.json()

        text = data["choices"][0]["message"]["content"]
        return _parse_json(text)

    async def _call_gemini(self, user_msg: str) -> dict:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY не встановлений")

        full_prompt = f"{SYSTEM_PROMPT}\n\n{user_msg}"
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 120,
                "responseMimeType": "application/json",
            },
        }

        # URL одним суцільним рядком
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

        async with self._session.post(url, json=payload) as resp:
            if resp.status != 200:
                # Читаємо детальну відповідь від сервера Google
                error_text = await resp.text()
                raise RuntimeError(f"Gemini HTTP {resp.status} - Деталі: {error_text}")
            data = await resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_json(text)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _build_prompt(task: LLMTask) -> str:
    lines = [f"Мерчант: {task.merchant_name}"]
    terms = (task.trade_terms or "").strip()
    lines.append(f"Умови: {terms[:300] if terms else '(не вказані)'}")
    if task.regex_result.risk_type:
        lines.append(f"Regex підозра: {task.regex_result.risk_type} — {task.regex_result.reason}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        return {"status": "UNKNOWN", "risk": "", "reason": "JSON parse error"}

    status = str(data.get("status", "UNKNOWN")).upper()
    if status not in ("OK", "SUSPICIOUS", "BLOCK"):
        status = "UNKNOWN"

    return {
        "status": status,
        "risk":   str(data.get("risk", "")),
        "reason": str(data.get("reason", ""))[:150],
    }