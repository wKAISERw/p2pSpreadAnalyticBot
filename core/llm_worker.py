# core/llm_worker.py
"""
Асинхронний LLM-воркер.

Поточний pipeline:
scanner -> RiskEngine -> asyncio.Queue -> llm_worker -> MerchantDB

Апгрейд:
- короткий структурований prompt замість сирого text dump
- використання score / matches / normalized_text з RegexResult
- жорсткіший JSON parsing
- кращі логи для подальшого тюнінгу правил
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from core.merchant_db import MerchantDB
from core.regex_analyzer import RegexResult

logger = logging.getLogger("LLMWorker")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()

def _setup_llm_log() -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    llm_log = logging.getLogger("LLMDecisions")
    if not llm_log.handlers:
        h = RotatingFileHandler(
            "logs/llm_decisions.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        llm_log.addHandler(h)
        llm_log.setLevel(logging.INFO)
        llm_log.propagate = False
    return llm_log

class RateLimitError(RuntimeError):
    pass


class PermanentModelError(RuntimeError):
    pass


class ProviderRateLimitError(RuntimeError):
    pass

LLM_LOG = _setup_llm_log()

LLM_WORKERS = 1
LLM_TIMEOUT = 7.0
MAX_QUEUE = 200
MAX_NORM_TERMS = 220
MAX_MATCHES_IN_PROMPT = 3
MAX_EXCERPT_LEN = 90

SYSTEM_PROMPT = """Ти — антифрод-система для P2P криптообміну UAH/USDT на ринку України.
Твоє завдання — визначити, чи умови мерчанта реально містять ризик, чи навпаки забороняють його.

ГОЛОВНІ РИЗИКИ:
- TRIANGLE: мерчант дозволяє або вимагає оплату від третьої особи, чужої картки, знайомого, дропа
- CASINO: казино, ставки, букмекери, процесинг, агрегатор
- CHAT_FIRST: просить написати до оплати
- SUSPICIOUS_BIZ: ФОП / IBAN / бізнес-рахунок у дивному контексті
- APPEAL_PRESSURE: тиск апеляцією, скаргою, погрози
- ANONYMOUS: анонімність, "без перевірки", cash-in, термінал
- EXTERNAL_LINK: вимагає перейти в Telegram, Viber, WhatsApp, Signal або інший зовнішній контакт

КРИТИЧНО:
- Розрізняй "згадує" і "вимагає".
- Якщо в тексті сказано "без третіх осіб", "лише зі своєї картки", "не пишіть у Telegram", "тільки в чаті біржі" — це НЕ BLOCK і зазвичай OK.
- BLOCK став лише якщо мерчант реально вимагає або допускає ризикову поведінку.
- Якщо кейс сумнівний, але не явний — SUSPICIOUS.
- Враховуй summary reviews, якщо вони є: високий % негативу та скарги підсилюють ризик.

ВІДПОВІДАЙ ВИКЛЮЧНО JSON:
{"status":"OK"|"SUSPICIOUS"|"BLOCK","risk":"TRIANGLE"|"CASINO"|"CHAT_FIRST"|"SUSPICIOUS_BIZ"|"APPEAL_PRESSURE"|"ANONYMOUS"|"EXTERNAL_LINK"|"BADREVIEWS"|"NONE","reason":"до 120 символів українською"}"""


@dataclass
class LLMTask:
    exchange: str
    merchant_id: str
    merchant_name: str
    trade_terms: str
    regex_result: RegexResult


class LLMWorkerPool:
    def __init__(self, db: MerchantDB):
        self._db = db
        self._queue: asyncio.Queue[LLMTask] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._pending: set[tuple[str, str]] = set()
        self._workers: list[asyncio.Task] = []
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
        logger.info(
            "LLMWorkerPool зупинено: оброблено=%d блоків=%d таймаутів=%d помилок=%d",
            s["processed"], s["blocks"], s["timeouts"], s["errors"]
        )

    def schedule(
        self,
        exchange: str,
        merchant_id: str,
        merchant_name: str,
        trade_terms: str,
        regex_result: RegexResult,
    ) -> bool:
        key = (exchange, merchant_id)
        if key in self._pending:
            return False

        task = LLMTask(exchange, merchant_id, merchant_name, trade_terms, regex_result)
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

                await asyncio.sleep(0.25)

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

        review_summary = await self._db.get_reviews_summary(task.exchange, task.merchant_id)
        result = await self._call_with_fallback(task, review_summary)
        self._stats["processed"] += 1

        verdict = result.get("status", "UNKNOWN").upper()
        risk_type = result.get("risk", "") or "NONE"
        reason = (result.get("reason", "") or "")[:150]
        source = result.get("_source", "unknown")

        if verdict == "BLOCK":
            self._stats["blocks"] += 1

        await self._db.save_verdict(
            task.exchange,
            task.merchant_id,
            task.merchant_name,
            task.trade_terms,
            verdict,
            risk_type,
            reason,
            source,
        )

        rr = task.regex_result
        score = getattr(rr, "score", 0)
        cats = _regex_categories(rr)

        LLM_LOG.info(
            "%-8s | %-10s | %-10s | %-14s | score=%-3s | cats=%-30s | %-8s | %s | %s",
            task.exchange,
            task.merchant_id[:10],
            verdict,
            risk_type,
            score,
            ",".join(cats)[:30] or "NONE",
            source,
            task.merchant_name[:20],
            reason[:80],
        )

        if verdict == "BLOCK":
            logger.warning(
                "🚫 LLM BLOCK [%s] %s [%s]: %s — %s",
                source, task.merchant_name, task.exchange, risk_type, reason
            )

    async def _call_with_fallback(self, task: LLMTask, review_summary: dict) -> dict:
        user_msg = _build_prompt(task, review_summary)

        try:
            result = await asyncio.wait_for(self._call_groq(user_msg), timeout=LLM_TIMEOUT)
            result["_source"] = "groq"
            return result
        except Exception as e:
            logger.error("❌ Groq помилка: %s", e)

        try:
            result = await asyncio.wait_for(self._call_gemini(user_msg), timeout=LLM_TIMEOUT)
            result["_source"] = f"gemini:{GEMINI_MODEL}"
            return result
        except PermanentModelError as e:
            logger.error("❌ Gemini config помилка: %s", e)
            return {
                "status": "UNKNOWN",
                "risk": "NONE",
                "reason": "Gemini model config error",
                "_source": "gemini_404",
            }
        except ProviderRateLimitError as e:
            logger.warning("⏳ Gemini rate limit: %s", e)
            return {
                "status": "UNKNOWN",
                "risk": "NONE",
                "reason": "Gemini rate limit",
                "_source": "gemini_429",
            }
        except asyncio.TimeoutError:
            self._stats["timeouts"] += 1
            logger.warning("⏳ Gemini timeout для %s", task.merchant_name)
            return {
                "status": "UNKNOWN",
                "risk": "NONE",
                "reason": "Gemini timeout",
                "_source": "gemini_timeout",
            }
        except Exception as e:
            logger.error("❌ Gemini помилка: %s", e)
            return {
                "status": "UNKNOWN",
                "risk": "NONE",
                "reason": "Gemini error",
                "_source": "gemini_error",
            }

    async def _call_groq(self, user_msg: str) -> dict:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GROQ_API_KEY не встановлений")

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": 120,
            "response_format": {"type": "json_object"},
        }

        async with self._session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        ) as resp:
            if resp.status == 429:
                raise RateLimitError("Groq rate limit 429")
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

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={api_key}"
        )

        async with self._session.post(url, json=payload) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"Gemini HTTP {resp.status} - Деталі: {error_text}")
            data = await resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_json(text)


def _build_prompt(task: LLMTask, review_summary: dict) -> str:
    rr = task.regex_result

    score = getattr(rr, "score", 0)
    norm_text = getattr(rr, "normalized_text", "") or (task.trade_terms or "").strip().lower()
    norm_text = norm_text[:MAX_NORM_TERMS] if norm_text else "(не вказані)"

    risk_type = getattr(rr, "risk_type", "") or "NONE"
    reason = getattr(rr, "reason", "") or "-"
    categories = _regex_categories(rr)
    excerpts = _top_excerpts(rr)

    pos = int(review_summary.get("positive", 0) or 0)
    neg = int(review_summary.get("negative", 0) or 0)
    neutral = int(review_summary.get("neutral", 0) or 0)
    total = pos + neg + neutral
    neg_pct = (neg / total * 100.0) if total > 0 else 0.0
    bad_texts = review_summary.get("bad_texts", []) or []

    lines = [
        f"Біржа: {task.exchange}",
        f"Мерчант: {task.merchant_name}",
        f"Merchant ID: {task.merchant_id}",
        f"Regex verdict: {rr.verdict}",
        f"Regex score: {score}",
        f"Regex main risk: {risk_type}",
        f"Regex reason: {reason[:140]}",
        f"Regex categories: {', '.join(categories) if categories else 'NONE'}",
        f"Умови: {norm_text}",
        "",
        "ПЕРЕВІР КОНТЕКСТ:",
        "- Чи merchant ВИМАГАЄ ризик, чи ЗАБОРОНЯЄ його?",
        "- Якщо написано 'без третіх осіб' або 'не пишіть у Telegram' — це безпечний контекст.",
        "",
        f"Reviews summary: pos={pos}, neg={neg}, neutral={neutral}, neg_pct={neg_pct:.1f}",
    ]

    if bad_texts:
        lines.append("Негативні відгуки:")
        for i, t in enumerate(bad_texts[:3], 1):
            lines.append(f"{i}. {str(t).replace(chr(10), ' ')[:120]}")

    if excerpts:
        lines.append("Regex фрагменти:")
        for i, ex in enumerate(excerpts, 1):
            lines.append(f"{i}. {ex}")

    lines.append(
        'Поверни JSON: {"status":"OK|SUSPICIOUS|BLOCK","risk":"...","reason":"чітко і коротко, що саме не так"}'
    )
    return "\n".join(lines)



def _regex_categories(regex_result: RegexResult) -> list[str]:
    matches = getattr(regex_result, "matches", []) or []
    seen = set()
    out = []
    for m in matches:
        cat = getattr(m, "category", "")
        weight = getattr(m, "weight", 0)
        if not cat or weight <= 0:
            continue
        if cat not in seen:
            seen.add(cat)
            out.append(cat)
    return out[:MAX_MATCHES_IN_PROMPT]


def _top_excerpts(regex_result: RegexResult) -> list[str]:
    matches = getattr(regex_result, "matches", []) or []
    pos = [m for m in matches if getattr(m, "weight", 0) > 0]
    pos.sort(key=lambda x: getattr(x, "weight", 0), reverse=True)

    out = []
    seen = set()
    for m in pos[:MAX_MATCHES_IN_PROMPT]:
        ex = (getattr(m, "excerpt", "") or "").replace("\n", " ").strip()
        ex = ex[:MAX_EXCERPT_LEN]
        if ex and ex not in seen:
            seen.add(ex)
            out.append(ex)
    return out


def _parse_json(text: str) -> dict:
    clean = (
        text.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(clean[start:end + 1])
            except json.JSONDecodeError:
                return {
                    "status": "UNKNOWN",
                    "risk": "NONE",
                    "reason": "JSON parse error",
                }
        else:
            return {
                "status": "UNKNOWN",
                "risk": "NONE",
                "reason": "JSON parse error",
            }

    status = str(data.get("status", "UNKNOWN")).upper()
    if status not in ("OK", "SUSPICIOUS", "BLOCK"):
        status = "UNKNOWN"

    risk = str(data.get("risk", "NONE")).upper()[:32]
    reason = str(data.get("reason", "")).strip()[:150]

    return {
        "status": status,
        "risk": risk or "NONE",
        "reason": reason or "Без пояснення",
    }
