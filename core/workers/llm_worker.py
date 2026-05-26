# core/workers/llm_worker.py
# =============================================================================
# БОЙОВА ВЕРСІЯ v2.0 (З підтримкою OpenAI GPT-4o-mini)
# =============================================================================
"""
Асинхронний LLM-воркер.

Зміни v2.0:
  - smart_truncate: зберігає початок + кінець trade_terms (scam часто в кінці)
  - bad_texts передаються в LLM навіть при UNAVAILABLE статусі
  - account_age_days в промпті
  - ДОДАНО: Інтеграція OpenAI (gpt-4o-mini) як основного/надійного fallback
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv

from core.utils.cache import TTLCache

load_dotenv()

from core.storage.merchant_db import MerchantDB
from core.analysis.regex_analyzer import RegexResult

logger = logging.getLogger("LLMWorker")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()  # 🚀 ДОДАНО OPENAI


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

LLM_WORKERS = 3
LLM_TIMEOUT = 12
MAX_QUEUE = 200
MAX_NORM_TERMS = 800
MAX_MATCHES_IN_PROMPT = 3
MAX_EXCERPT_LEN = 90

SYSTEM_PROMPT = """Ти — антифрод-система для P2P криптообміну UAH/USDT на ринку України (Deep Research Engine v5.2).
Твоє завдання — визначити, чи умови мерчанта, його відгуки або математика стакану містять ризик.

ГОЛОВНІ РИЗИКИ (допустимі значення для поля risk):
- TRIANGLE: вимагає або допускає оплату від третьої особи, дропа.
- THIRD_PARTY_HINT: двозначна згадка третіх осіб (уважно читай контекст).
- CASINO: казино, ставки, букмекери, процесинг.
- CHAT_FIRST: просить написати до оплати в чат.
- SUSPICIOUS_BIZ: ФОП / бізнес-рахунок у дивному контексті.
- APPEAL_PRESSURE: тиск апеляцією, скаргою, погрози.
- ANONYMOUS: анонімність, "без перевірки", cash-in, термінал.
- EXTERNAL_LINK: вимагає перейти в Telegram, Viber, Signal.
- FINCRIME: фінмон, AML, брудні гроші, обнал, сірі схеми.
- MIDDLEMAN: використання посередника, номіналу, прокладки.
- CHARGEBACK: погроза рефандом, чарджбеком, поверненням через банк.
- NO_COMMENTS: жорстка вимога нічого не писати в коментарях до платежу.
- RECEIPT_REQUIRED: вимога чеку/квитанції як інструмент маніпуляції.
- BOT_API: використання скриптів/процесингу (прапори API_REPLENISH).

КРИТИЧНО:
- 🚀 РОЗРІЗНЯЙ "ЗАБОРОНЯЄ" ТА "ДОПУСКАЄ ВИНЯТКИ". Якщо мерчант чітко пише "тільки своя карта", "без 3-х осіб", "з 3 особами не працюю" — ЦЕ БЕЗПЕЧНО (OK). НЕ видумуй прихованих ризиків!
- 🧾 КВИТАНЦІЇ ТА ЧЕКИ: Прохання надати чек — це НОРМАЛЬНО. НІКОЛИ не блокуй тільки за вимогу чеку.
- 👨‍👩‍👦 ОДНОФАМІЛЬЦІ: Якщо мерчант допускає оплату від родичів ВИКЛЮЧНО з ТИМ САМИМ ПРІЗВИЩЕМ — БЕЗПЕЧНО.
- ПОВЕДІНКА СТАКАНУ: Якщо є прапори "API_REPLENISH" або "CROSS_EXCHANGE_BOT" — це 100% бот-процесинг. ПОВИНЕН ставити verdict: BLOCK, risk: BOT_API.
- НОВИЙ АКАУНТ: Якщо акаунт молодший 14 днів, а кількість угод підозріло велика — підвищуй ризик.
- ФІКСОВАНІ ЛІМІТИ: Якщо min_limit ≈ max_limit (фіксована сума) — це РІДКІСТЬ серед звичайних продавців. Разом з 100% рейтингом і великою кількістю угод = бот-процесинг (BOT_API). Разом з підозрілими умовами = трикутна схема (TRIANGLE). Один факт фіксованих лімітів без інших сигналів — verdict: SUSPICIOUS.
- 🚨 ЖОРСТКИЙ ПРІОРИТЕТ СКАМ-ВІДГУКІВ: Якщо серед багатьох негативних відгуків (напр., 9 про "повільно", "довго") є ХОЧА Б ОДИН про "шахрай", "кидала", "рефанд", "трикутник" — ЦЬОМУ ОДНОМУ ВІДГУКУ НАДАЄТЬСЯ АБСОЛЮТНИЙ ПРІОРИТЕТ. Вердикт обов'язково має бути BLOCK!
- Враховуй СТАТИСТИКУ! Верифікований мерчант з >500 угодами і >95% — його жорсткі вимоги щодо безпеки є нормою.

🔍 ОБОВ'ЯЗКОВО АНАЛІЗУЙ ВІДГУКИ:
- ЗАВЖДИ коментуй стан відгуків у thought_process: скільки позитивних/негативних, чи є тексти поганих відгуків, що саме там написано.
- Відгуки позначені 🟡 — це збіг з відомими regex-патернами (ключові слова). Але це лише ПІДКАЗКА, не вичерпний аналіз.
- Відгуки БЕЗ маркера 🟡 — regex не знайшов відомих патернів, але це НЕ означає що вони безпечні. ОБОВ'ЯЗКОВО прочитай кожен і визнач: чи є там скарги на обман, проблеми з оплатою, агресію, маніпуляції, невиконання зобов'язань.
- Якщо відгуки чисті (neg=0 або дуже низький %) — напиши це явно: "Відгуки чисті, neg=X/Y, загроз не виявлено".
- Якщо відгуки відсутні — зазнач це як фактор невизначеності.
- Якщо є негативні тексти — проаналізуй їх зміст (скам, дроп, кинув — це BLOCK; повільно, не відповідає — це м'який сигнал).
- Якщо є негативні тексти БЕЗ відомих ключових слів — ОБОВ'ЯЗКОВО вкажи у reason що саме ти там побачив. Не ігноруй їх!
- Якщо мерчант підозрілий за поведінкою, але відгуки повністю чисті — це пом'якшуючий фактор, зазнач це.
- 🚨 ЖОРСТКИЙ ПРІОРИТЕТ СКАМ-ВІДГУКІВ: Якщо серед багатьох негативних відгуків (напр., 9 про "повільно", "довго") є ХОЧА Б ОДИН про "шахрай", "кидала", "рефанд", "трикутник" — ЦЬОМУ ОДНОМУ ВІДГУКУ НАДАЄТЬСЯ АБСОЛЮТНИЙ ПРІОРИТЕТ. Вердикт обов'язково має бути BLOCK!
- 📝 СУМАРИЗАЦІЯ ВІДГУКІВ: У поле "reviews_analysis" напиши коротке summary (вижимку) що саме пишуть у текстах негативних відгуків (від себе, як аналітик).

🚫 ЧЕСНІСТЬ ЩОДО ВІДГУКІВ:
- Якщо написано "ТЕКСТИ ВІДГУКІВ НЕДОСТУПНІ" або "ОЦІНКА з completion rate" — НЕ вигадуй аналіз відгуків.
- Пиши чесно: "тексти негативних відгуків недоступні, причини ~N негативних невідомі (можливо скам, можливо технічні проблеми)."
- НЕ плутай зірвані угоди (зі статистики) з негативними відгуками (з Review). Це РІЗНІ метрики.
- Якщо тексти є — вкажи КОНКРЕТНО за що були негативні відгуки: "скаржаться на затримку 3 години", "звинувачують у шахрайстві", "не повертає кошти" тощо.
- Якщо є тексти негативних відгуків — вони ВАЖЛИВІШІ за кількість. Один відгук "шахрай, кинув на 5000 грн" важить більше ніж 50 позитивних.

ВІДПОВІДАЙ ВИКЛЮЧНО JSON (без жодного тексту поза ним):
{"thought_process":"детальний логічний ланцюжок: 1) аналіз умов 2) аналіз відгуків 3) аналіз поведінки 4) загальний висновок","status":"OK"|"SUSPICIOUS"|"BLOCK","risk":"ОДНА_З_КАТЕГОРІЙ","reason":"розгорнутий підсумок (2-3 речення): що виявлено, стан відгуків, чому саме такий вердикт","trade_recommendation":"APPROVE"|"CONDITIONAL"|"REJECT","terms_summary":"коротка вижимка умов мерчанта (1-2 речення): ключові вимоги, ліміти, особливості, нюанси. Без оцінки ризику — лише факти з умов.","reviews_analysis":"текстова сумаризація негативних відгуків: скільки про затримки, чи є скарги на скам"}

ПОЛЕ trade_recommendation — ОБОВ'ЯЗКОВЕ. Пряма відповідь: чи варто проводити P2P-угоду з цим мерчантом ЗАРАЗ?
APPROVE     — торгувати можна. Ризиків немає або вони мінімальні.
CONDITIONAL — можна, але з застереженням (новий акаунт, м'який SUSPICIOUS, мало угод). Бот знизить суму або буде обережнішим.
REJECT      — НЕ торгувати. Чіткі ознаки скаму, бот-процесингу або небезпеки для коштів.
Правило відповідності: status=OK → APPROVE; status=SUSPICIOUS → CONDITIONAL; status=BLOCK → ЗАВЖДИ REJECT.

ПОЛЕ terms_summary — ОБОВ'ЯЗКОВЕ. Коротка вижимка умов мерчанта БЕЗ оцінки ризику:
- Тільки факти: які банки приймає, вимоги до оплати, ліміти часу, обмеження, особливості.
- НЕ дублюй reason — terms_summary це ПРО УМОВИ, reason це ПРО РИЗИК.
- Якщо умов немає — "Умови не вказані."
- Приклад: "Тільки Моно/Приват, оплата протягом 15 хв, ПІБ має збігатися, без 3-х осіб."
- Максимум 2 короткі речення.

ПОЛЕ reviews_analysis — ОБОВ'ЯЗКОВЕ. Якщо відгуків нема або вони чисті, напиши "Відгуки чисті, загроз зі сторони коментарів не виявлено". Якщо їх не завантажено - "Тексти відгуків недоступні". Сумаризуй на що скаржаться люди."""


@dataclass
class LLMTask:
    exchange: str
    merchant_id: str
    merchant_name: str
    trade_terms: str
    regex_result: RegexResult
    finish_rate: float
    month_order_count: int
    is_verified: bool
    min_limit: float
    max_limit: float
    behavior_flags: list[str] = field(default_factory=list)
    account_age_days: int = 0
    review_summary: dict = field(default_factory=dict)


class LLMWorkerPool:
    def __init__(self, db: MerchantDB):
        self._db = db
        self._queue: asyncio.Queue[LLMTask] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._pending: set[tuple[str, str]] = set()
        self._workers: list[asyncio.Task] = []
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats = {"processed": 0, "blocks": 0, "timeouts": 0, "errors": 0}
        self._recent_calls = TTLCache(ttl_seconds=600)

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
            s["processed"], s["blocks"], s["timeouts"], s["errors"],
        )

    def schedule(
            self,
            exchange: str,
            merchant_id: str,
            merchant_name: str,
            trade_terms: str,
            regex_result: RegexResult,
            finish_rate: float = 0.0,
            month_order_count: int = 0,
            is_verified: bool = False,
            min_limit: float = 0.0,
            max_limit: float = 0.0,
            behavior_flags: list[str] = None,
            account_age_days: int = 0,
            review_summary: dict = None,
    ) -> bool:
        cache_key = f"{exchange}:{merchant_id}"
        key = (exchange, merchant_id)
        if key in self._pending or self._recent_calls.seen(cache_key):
            return False
        task = LLMTask(
            exchange=exchange,
            merchant_id=merchant_id,
            merchant_name=merchant_name,
            trade_terms=trade_terms,
            regex_result=regex_result,
            finish_rate=finish_rate,
            month_order_count=month_order_count,
            is_verified=is_verified,
            min_limit=min_limit,
            max_limit=max_limit,
            behavior_flags=behavior_flags or [],
            account_age_days=account_age_days,
            review_summary=review_summary or {},
        )
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

        if task.review_summary:
            review_summary = task.review_summary
        else:
            review_summary = await self._db.get_reviews_summary(task.exchange, task.merchant_id)

        result = await self._call_with_fallback(task, review_summary)
        self._stats["processed"] += 1

        verdict = result.get("status", "UNKNOWN").upper()
        risk_type = result.get("risk", "") or "NONE"
        reason = (result.get("reason", "") or "")[:900]
        source = result.get("source", "unknown")
        terms_summary = (result.get("terms_summary", "") or "")[:300]

        if verdict == "BLOCK":
            self._stats["blocks"] += 1

        trade_recommendation = result.get("trade_recommendation", "CONDITIONAL")
        reviews_analysis = (result.get("reviews_analysis", "") or "")[:500]

        await self._db.save_verdict(
            task.exchange, task.merchant_id, task.merchant_name,
            task.trade_terms, verdict, risk_type, reason, source,
            trade_recommendation=trade_recommendation,
            terms_summary=terms_summary,
            reviews_analysis=reviews_analysis
        )

        rr = task.regex_result
        score = getattr(rr, "score", 0)
        cats = _regex_categories(rr)

        LLM_LOG.info(
            "%-8s | %-11s | %-10s | %-14s | score=%-3s | cats=%-35s | %-8s | %s | %s",
            task.exchange, task.merchant_id[:11], verdict, risk_type, score,
            ",".join(cats)[:35] or "NONE", source, task.merchant_name[:40], reason[:900],
        )

        if verdict == "BLOCK":
            logger.warning(
                "🚫 LLM BLOCK [%s] %s [%s]: %s — %s",
                source, task.merchant_name, task.exchange, risk_type, reason,
            )

        self._recent_calls.mark(f"{task.exchange}:{task.merchant_id}")

    # ── Groq / OpenAI / Gemini cooldown (class-level) ─────────────────────────
    _groq_cooldown_until: float = 0.0
    _groq_consecutive_429: int = 0
    _openai_cooldown_until: float = 0.0  # 🚀 ДОДАНО OPENAI
    _gemini_cooldown_until: float = 0.0

    async def _call_with_fallback(self, task: LLMTask, review_summary: dict) -> dict:
        import random
        import time as _time

        user_msg = _build_prompt(task, review_summary)

        now = _time.monotonic()
        groq_available = LLMWorkerPool._groq_cooldown_until <= now
        gemini_available = LLMWorkerPool._gemini_cooldown_until <= now
        openai_available = LLMWorkerPool._openai_cooldown_until <= now

        # В callwithfallback — для кожного провайдера:

        # Groq
        if groq_available:
            try:
                result = await asyncio.wait_for(
                    self._call_groq(user_msg), timeout=LLM_TIMEOUT
                )
                result["source"] = f"groq_{GROQ_MODEL}"
                LLMWorkerPool._groq_consecutive_429 = 0
                return result
            except RateLimitError:
                n = LLMWorkerPool._groq_consecutive_429 + 1
                LLMWorkerPool._groq_consecutive_429 = n
                wait = min(30.0 * (2 ** min(n - 1, 4)), 300.0)
                LLMWorkerPool._groq_cooldown_until = _time.monotonic() + wait
                logger.warning("Groq 429 (серія=%d) cooldown=%.0fs → Gemini", n, wait)
            except asyncio.TimeoutError:
                self._stats["timeouts"] += 1
                logger.warning("Groq timeout: %s", task.merchant_name)
            except Exception as e:
                logger.error("Groq помилка: %s", e)

        # Gemini
        if gemini_available:
            try:
                result = await asyncio.wait_for(
                    self._call_gemini(user_msg), timeout=LLM_TIMEOUT
                )
                result["source"] = f"gemini_{GEMINI_MODEL}"
                return result
            except PermanentModelError as e:
                logger.error("Gemini 404: %s → OpenAI", e)
                # НЕ return — падаємо на OpenAI
            except ProviderRateLimitError:
                LLMWorkerPool._gemini_cooldown_until = _time.monotonic() + 60.0
                logger.warning("Gemini 429 → OpenAI")
            except asyncio.TimeoutError:
                self._stats["timeouts"] += 1
                logger.warning("Gemini timeout: %s", task.merchant_name)
            except Exception as e:
                logger.error("Gemini помилка: %s", e)

        # OpenAI — останній резерв
        if openai_available:
            try:
                result = await asyncio.wait_for(
                    self._call_openai(user_msg), timeout=LLM_TIMEOUT
                )
                result["source"] = f"openai_{OPENAI_MODEL}"
                return result
            except PermanentModelError:
                pass
            except ProviderRateLimitError:
                LLMWorkerPool._openai_cooldown_until = _time.monotonic() + 30.0
                logger.warning("OpenAI 429")
            except asyncio.TimeoutError:
                self._stats["timeouts"] += 1
                logger.warning("OpenAI timeout: %s", task.merchant_name)
            except Exception as e:
                logger.error("OpenAI помилка: %s", e)

        self._stats["timeouts"] += 1
        return {
            "status": "UNKNOWN", "risk": "NONE",
            "reason": "All APIs unavailable",
            "source": "cooldown_skip",
            "trade_recommendation": "CONDITIONAL"
        }

    async def _call_groq(self, user_msg: str) -> dict:
        groq_key = os.getenv("GROQ_API_KEY", "")
        if not groq_key:
            raise PermanentModelError("GROQ_API_KEY не встановлено")
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"}
        }
        async with self._session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {groq_key}"},
        ) as resp:
            if resp.status == 429:
                raise RateLimitError("Groq 429")
            if resp.status >= 500:
                raise RuntimeError(f"Groq server error {resp.status}")
            data = await resp.json()
        text = data["choices"][0]["message"]["content"]
        return _parse_json(text)

    async def _call_openai(self, user_msg: str) -> dict:
        # 🚀 ДОДАНО: Метод виклику OpenAI API
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not openai_key:
            raise PermanentModelError("OPENAI_API_KEY не встановлено")

        payload = {
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"}
        }

        async with self._session.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {openai_key}"},
        ) as resp:
            if resp.status == 429:
                raise ProviderRateLimitError("OpenAI 429")
            if resp.status >= 500:
                raise RuntimeError(f"OpenAI server error {resp.status}")
            data = await resp.json()

        text = data["choices"][0]["message"]["content"]
        return _parse_json(text)

    async def _call_gemini(self, user_msg: str) -> dict:
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            raise PermanentModelError("GEMINI_API_KEY")

        full_prompt = f"{SYSTEM_PROMPT}\n\n{user_msg}"
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 1200,
                "responseMimeType": "application/json",
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={gemini_key}"

        async with self._session.post(url, json=payload) as resp:
            if resp.status == 429:
                raise ProviderRateLimitError("Gemini 429")
            if resp.status == 404:
                raise PermanentModelError(f"Gemini model not found: {GEMINI_MODEL}")
            if resp.status >= 500:
                raise RuntimeError(f"Gemini server error {resp.status}")
            data = await resp.json()

        # ← Захист від thinking моделей (можуть мати кілька parts)
        try:
            candidates = data.get("candidates", [])
            parts = candidates[0]["content"]["parts"]
            # Шукаємо part де є text (не thinking)
            text = next(
                (p["text"] for p in parts if "text" in p and not p.get("thought")),
                parts[-1].get("text", "")  # fallback
            )
        except (IndexError, KeyError) as e:
            raise RuntimeError(f"Gemini response parse error: {e}, data={data}")

        return _parse_json(text)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _smart_truncate(text: str, max_len: int = MAX_NORM_TERMS) -> str:
    if not text or len(text) <= max_len:
        return text or "(не вказані)"
    head_len = (max_len * 2) // 3
    tail_len = max_len - head_len
    head = text[:head_len]
    tail = text[-tail_len:]
    return f"{head}…[скорочено]…{tail}"


def _build_account_age_note(task: LLMTask) -> str:
    days = task.account_age_days
    if days <= 0:
        return ""
    if days < 7:
        return f" ⚠️ ДУЖЕ НОВИЙ АКАУНТ ({days} днів)!"
    if days < 14 and task.month_order_count > 50:
        return f" ⚠️ НОВИЙ АКАУНТ ({days} днів) з підозріло великою активністю!"
    if days < 30:
        return f" (акаунт {days} днів — молодий)"
    return f" (акаунт {days} днів)"


def _build_behavior_block(task: LLMTask) -> list[str]:
    flags = task.behavior_flags or []
    if not flags:
        return ["ПОВЕДІНКА: Нормальна"]

    lines = ["ПОВЕДІНКА (аномалії):"]
    for f in flags:
        if f.startswith("API_REPLENISH:"):
            n = f.split(":", 1)[1] if ":" in f else "?"
            lines.append(f"  - БОТ-АВТО-ПОПОВНЕННЯ: ліміти стабільні {n} циклів, кількість угод зростає (скрипт)")
        elif f.startswith("STATIC_DROP:"):
            n = f.split(":", 1)[1]
            lines.append(f"  - СТАТИЧНИЙ ДРОП: ліміти min=max, незмінні {n} циклів, угоди не ростуть")
        elif f.startswith("VELOCITY_SPIKE:"):
            v = f.split(":", 1)[1]
            lines.append(f"  - АНОМАЛЬНА ШВИДКІСТЬ: {v} угод/год (норма <20/год)")
        elif f == "EXACT_LIMITS":
            lines.append(f"  - ФІКСОВАНА СУМА: min_limit ≈ max_limit ({task.min_limit}–{task.max_limit} UAH)")
        elif f.startswith("FLICKER_RELIST:"):
            n = f.split(":", 1)[1]
            lines.append(f"  - РІЛІСТИНГ: зникав і повертався з тими ж умовами {n} раз (маніпуляція)")
        elif f.startswith("CROSS_EXCHANGE_BOT:"):
            ex = f.split(":", 2)[-1] if f.count(":") >= 2 else ""
            lines.append(f"  - КЛОН НА БІРЖАХ: однакові ліміти знайдено на {ex}")
        elif f.startswith("BEHAVIOR_BOTLIKE:"):
            s = f.split(":", 1)[1]
            lines.append(f"  - ЗАГАЛЬНА ПІДОЗРА НА БОТА: score={s}")
        elif f.startswith("ALWAYS_ONLINE_24H:"):
            h = f.split(":", 1)[1]
            lines.append(f"  - АКТИВНИЙ 24/7: {h} різних годин доби за 48h (людина так не працює)")
        elif f.startswith("PRICE_TRAP:"):
            d = f.split(":", 1)[1]
            lines.append(f"  - ЦІНОВА ПАСТКА: ціна {d} нижче медіани ринку (honey pot)")
        else:
            lines.append(f"  - {f}")
    return lines


def _build_prompt(task: LLMTask, review_summary: dict) -> str:
    rr = task.regex_result

    score = getattr(rr, "score", 0)
    norm_text = getattr(rr, "normalized_text", "") or (task.trade_terms or "").strip().lower()
    norm_text = _smart_truncate(norm_text, MAX_NORM_TERMS)

    risk_type = getattr(rr, "risk_type", "") or "NONE"
    categories = _regex_categories(rr)
    excerpts = _top_excerpts(rr)

    rev_status = review_summary.get("status", "OK") if review_summary else "UNKNOWN"
    rev_error_reason = (review_summary.get("error_reason", "") if review_summary else "") or ""
    pos = int((review_summary or {}).get("positive", 0) or 0)
    neg = int((review_summary or {}).get("negative", 0) or 0)
    neutral = int((review_summary or {}).get("neutral", 0) or 0)
    total = pos + neg + neutral
    neg_pct = (neg / total * 100.0) if total > 0 else 0.0
    bad_texts = (review_summary or {}).get("bad_texts", []) or []
    is_estimated = bool((review_summary or {}).get("estimated_from_stats"))

    failed_orders = int(task.month_order_count * (100.0 - task.finish_rate) / 100.0)
    age_note = _build_account_age_note(task)

    lines = [
        f"Біржа: {task.exchange}",
        f"Мерчант: {task.merchant_name} (ID: {task.merchant_id})",
        "СТАТИСТИКА:",
        f"- Угод за місяць: {task.month_order_count}",
        f"- Успішність: {task.finish_rate}% (~{failed_orders} зірваних/проблемних угод)",
        f"- Верифікація: {'ТАК' if task.is_verified else 'НІ'}",
        f"- Поточні ліміти: {task.min_limit} - {task.max_limit} UAH",
    ]

    if age_note:
        lines.append(f"- Вік акаунту:{age_note}")

    lines += _build_behavior_block(task)

    lines += [
        "",
        f"Regex verdict: {rr.verdict} (Score: {score})",
        f"Regex main risk: {risk_type}",
        f"Regex categories: {', '.join(categories) if categories else 'NONE'}",
        f"Умови: {norm_text}",
        "",
        "ПЕРЕВІР КОНТЕКСТ:",
        "- Зважай на статистику (зірвані угоди). Якщо умов немає (Regex мовчить), але є сотні зірваних угод + хоч один поганий відгук = це BLOCK.",
        "- Зважай на рейтинг мерчанта. Трастовим мерчантам дозволено жорсткіше формулювати безпекові вимоги.",
        "- Якщо написано 'без третіх осіб' або 'не пишіть у Telegram' — це безпечний контекст.",
        "",
    ]

    _NO_REVIEW_EXCHANGES = {"Wallet", "CryptoBot"}
    if rev_status != "OK" or rev_error_reason:
        diag = f"Reviews diagnostics: status={rev_status}"
        if rev_error_reason:
            diag += f", reason={rev_error_reason[:220]}"
        lines.append(diag)

    from config.runtime import runtime_config
    require_sessions = runtime_config.get("require_sessions", "true") == "true"
    if not require_sessions:
        lines.append(
            "⚠️ РЕЖИМ ІГНОРУВАННЯ СЕСІЙ АКТИВНИЙ: Сесії вимкнені користувачем. Доступ до відгуків не очікується. НЕ вважайте відсутність відгуків підозрілим сигналом."
        )
    elif rev_status in ("NO_SESSION", "SESSION_EXPIRED"):
        lines.append(
            "⚠️ ТЕХНІЧНА ПОМИЛКА СЕСІЇ: Сесії увімкнені, але наразі недійсні (NO_SESSION/SESSION_EXPIRED). Тексти відгуків недоступні через технічну проблему з сесією. НЕ вважайте відсутність відгуків підозрілим фактором мерчанта."
        )

    # Пояснення доступності відгуків та інструкції для LLM
    if task.exchange in _NO_REVIEW_EXCHANGES or rev_status == "NOT_SUPPORTED":
        lines.append(
            f"Reviews: Біржа {task.exchange} не має API відгуків. Оцінюй ТІЛЬКИ за умовами, поведінкою та статистикою. НЕ штрафуй за відсутність відгуків."
        )
    elif rev_status == "NO_AUTH":
        lines.append(
            "Reviews: API відгуків потребує автентифікації — тимчасово недоступний. Оцінюй за умовами та поведінкою. НЕ вважай відсутність відгуків фактором ризику."
        )
    elif rev_status in ("NO_SESSION", "SESSION_EXPIRED"):
        lines.append(
            f"Reviews (⚠️ ТЕХНІЧНА ПОМИЛКА СЕСІЇ): Тексти відгуків недоступні через те, що браузерна сесія наразі не перехоплена або протухла (status={rev_status}). "
            "Це технічна проблема нашої системи, а не підозріла поведінка мерчанта. "
            "Якщо за статистикою є негативні відгуки, напиши чесно у thought_process: 'тексти негативних відгуків недоступні через технічну помилку сесії (NO_SESSION)'. "
            "НЕ вважайте відсутність відгуків підозрілим фактором і НЕ штрафуйте мерчанта за це."
        )
        if is_estimated and total > 0:
            lines.append(
                f"Статистика відгуків (⚠️ ОЦІНКА з completion rate): ~pos≈{pos}, ~neg≈{neg}, ~neg%≈{neg_pct:.1f}%"
            )
            if neg > 0:
                lines.append(
                    "❌ ТЕКСТИ ВІДГУКІВ НЕДОСТУПНІ (немає активної сесії). Ми НЕ ЗНАЄМО причини негативних відгуків. "
                    "Вкажи це ЧЕСНО у thought_process: 'тексти відгуків недоступні, причини neg≈X невідомі'. НЕ придумуй деталі відгуків!"
                )
            else:
                lines.append(
                    "Reviews: Жодного негативного відгуку не прогнозується на основі статистики профілю."
                )
    elif rev_status in ("API_ERROR", "UNAVAILABLE"):
        lines.append(
            f"Reviews (⚠️ ТЕХНІЧНА ПОМИЛКА API): Біржа повернула технічну помилку API при спробі завантажити відгуки (status={rev_status}). "
            "Тексти відгуків тимчасово недоступні через збій API. "
            "Напиши чесно у thought_process: 'тексти негативних відгуків недоступні через тимчасовий збій API' і НЕ вважайте це підозрілим фактором мерчанта."
        )
    elif rev_status == "NO_FEEDBACK":
        lines.append(
            "Reviews: API біржі повернув 0 відгуків для цього мерчанта. Відгуків на біржі взагалі немає. "
            "Якщо мерчант має дуже багато угод (наприклад, >200 угод), але 0 відгуків, це підозріло (можливе скидання або накрутка профілю). "
            "Якщо угод мало, це нормальна ситуація. Опиши це в thought_process."
        )
    else:
        # Успішно завантажені відгуки (rev_status == "OK")
        if total > 0:
            est_note = " (оцінено зі статистики профілю)" if is_estimated else ""
            lines.append(
                f"Reviews: Успішно завантажено відгуки{est_note}. Статистика: pos={pos}, neg={neg}, neutral={neutral}, neg%={neg_pct:.1f}%"
            )
            if neg == 0:
                lines.append(
                    "⬆️ ВІДГУКИ ПОВНІСТЮ ЧИСТІ: Жодного негативного відгуку! Мейкер має бездоганну репутацію. "
                    "Обов'язково вкажи у thought_process, reason та reviews_analysis: 'відгуки чисті, негативні відгуки відсутні, репутація чиста'."
                )
            elif neg_pct < 3.0:
                lines.append(f"⬆️ ВІДГУКИ ПЕРЕВАЖНО ЧИСТІ: Лише {neg} негативних ({neg_pct:.1f}%).")
        else:
            lines.append(
                "Reviews: Мерчант новий або ще не має відгуків. Оцінюй за умовами та поведінкою. "
                "Зазнач це у thought_process як фактор невизначеності (НЕ як ризик)."
            )

    if bad_texts:
        flagged_count = sum(1 for t in bad_texts if isinstance(t, dict) and t.get("keyword_flagged"))
        unflagged_count = len(bad_texts) - flagged_count
        lines.append(f"НЕГАТИВНІ ВІДГУКИ ({len(bad_texts)} шт, з них {flagged_count} з ключовими словами):")
        lines.append("  🟡 = збіг з відомими ключовими словами/патернами (regex); без маркера = відгук без тригерів — проаналізуй САМОСТІЙНО.")
        for i, t in enumerate(bad_texts[:10], 1):
            if isinstance(t, dict):
                text = str(t.get("text", "")).replace("\n", " ").strip()[:250]
                score = t.get("score", 0)
                cats = t.get("categories", [])
                excerpt = str(t.get("excerpt", "")).replace("\n", " ").strip()[:100]
                is_flagged = t.get("keyword_flagged", False)
                marker = "🟡" if is_flagged else "  "
                if cats:
                    cat_str = ", ".join(cats)
                    lines.append(f"  {marker} {i}. [{cat_str}, score={score}] {text}")
                else:
                    lines.append(f"  {marker} {i}. {text}")
                if excerpt and excerpt not in text:
                    lines.append(f"     ↳ ключовий фрагмент: «{excerpt}»")
            else:
                clean_t = str(t).replace("\n", " ").strip()
                lines.append(f"     {i}. {clean_t[:250]}")
        lines.append(
            "  ⚠️ ПРОАНАЛІЗУЙ ЗМІСТ КОЖНОГО негативного відгуку (особливо відповіді мейкера, якщо вони є, вказані після '| Відповідь мейкера:'). "
            "ОБОВ'ЯЗКОВО детально опиши характер та зміст цих конкретних скарг у полях 'reason' та 'reviews_analysis' (наприклад: скаржаться на затримки, звинувачують у податках/комісіях, чи є скарги на скам). "
            "Не ігноруй деталі! Якщо відгуки про шахрайство/трикутники/рефанди -> ставити BLOCK."
        )
    elif neg > 0:
        # Є негативні, але немає текстів (наприклад, не завантажилися)
        lines.append(
            f"❌ ТЕКСТИ ВІДГУКІВ НЕДОСТУПНІ: Є {neg} негативних відгуків у статистиці, але їх тексти відсутні. "
            f"Причини негативних відгуків НЕВІДОМІ. Обов'язково вкажи це чесно у thought_process та reason: "
            f"'тексти негативних відгуків недоступні, причини {neg} негативних відгуків невідомі'. НЕ придумуй зміст скарг!"
        )

    if excerpts:
        lines.append("Regex фрагменти:")
        for i, ex in enumerate(excerpts, 1):
            lines.append(f"  {i}. {ex}")

    lines.append(
        '\nПоверни JSON: {"thought_process":"детальний аналіз: умови → відгуки → поведінка → висновок","status":"OK|SUSPICIOUS|BLOCK","risk":"...","reason":"2-3 речення: що виявлено, стан відгуків, обґрунтування вердикту","trade_recommendation":"APPROVE|CONDITIONAL|REJECT","terms_summary":"коротка вижимка умов мерчанта (факти, без оцінки ризику)","reviews_analysis":"текстова сумаризація негативних відгуків: скільки про затримки, чи є скарги на скам"}'
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _regex_categories(regex_result: RegexResult) -> list[str]:
    matches = getattr(regex_result, "matches", []) or []
    seen, out = set(), []
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
    out, seen = [], set()
    for m in pos[:MAX_MATCHES_IN_PROMPT]:
        ex = (getattr(m, "excerpt", "") or "").replace("\n", " ").strip()[:MAX_EXCERPT_LEN]
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
                logger.error(f"❌ JSON parse error. Сирий текст: {text!r}")
                return {"status": "UNKNOWN", "risk": "NONE", "reason": "JSON parse error"}
        else:
            logger.error(f"❌ JSON parse error (немає дужок). Сирий текст: {text!r}")
            return {"status": "UNKNOWN", "risk": "NONE", "reason": "JSON parse error"}

    status = str(data.get("status", "UNKNOWN")).upper()
    if status not in ("OK", "SUSPICIOUS", "BLOCK"):
        status = "UNKNOWN"
    risk = str(data.get("risk", "NONE")).upper()[:32]
    reason = str(data.get("reason", "")).strip()[:900]

    thought = data.get("thought_process", "")
    if thought:
        logger.debug(f"🧠 LLM Thoughts: {thought}")
        LLM_LOG.info("🧠 THOUGHT | %s", str(thought)[:3000])

    # ── trade_recommendation ─────────────────────────────────────────────────
    _raw_rec = str(data.get("trade_recommendation", "")).strip().upper()
    _VALID_RECS = ("APPROVE", "CONDITIONAL", "REJECT")
    if _raw_rec not in _VALID_RECS:
        _fallback = {"OK": "APPROVE", "SUSPICIOUS": "CONDITIONAL", "BLOCK": "REJECT"}
        trade_recommendation = _fallback.get(status, "CONDITIONAL")
        if _raw_rec:
            logger.warning(
                "[LLM] Невідомий trade_recommendation=%r, fallback→%s (status=%s)",
                _raw_rec, trade_recommendation, status,
            )
    else:
        trade_recommendation = _raw_rec
    # BLOCK завжди → REJECT (безпека)
    if status == "BLOCK" and trade_recommendation != "REJECT":
        logger.warning("[LLM] trade_recommendation конфліктує зі status=BLOCK → примусово REJECT")
        trade_recommendation = "REJECT"

    terms_summary = str(data.get("terms_summary", "")).strip()[:300]

    return {
        "status": status,
        "risk": risk or "NONE",
        "reason": reason or "Без пояснення",
        "trade_recommendation": trade_recommendation,
        "terms_summary": terms_summary,
    }
