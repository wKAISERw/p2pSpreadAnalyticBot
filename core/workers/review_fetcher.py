# core/workers/review_fetcher.py
"""
ReviewFetcher — завантаження відгуків мерчантів на вимогу.

  1. pos/neg/neutral беремо з профілю мерчанта (не нулі)
  2. Один шлях: fetch_now(), викликається лениво з RiskEngine для
     реальних кандидатів спреду. Фонова черга прибрана — див. докстрінг класу
  3. Окремий аналіз тексту відгуків по review_only правилах
  4. Degraded mode: 3 відмови поспіль → cooldown на біржу
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, TYPE_CHECKING

from core.storage.merchant_db import MerchantDB
from core.analysis.rules import ALL_RULES
from core.risk.matcher import match_text
from core.risk.signals import SCOPE_REVIEWS
from core.utils.tasks import spawn

if TYPE_CHECKING:
    from infrastructure.http.binance_client import BinanceClient
    from infrastructure.http.bybit_p2p_client import BybitP2PClient
    from infrastructure.http.okx_client import OkxClient
    from infrastructure.http.mexc_client import MexcClient
    from infrastructure.http.cryptobot_client import CryptoBotWebClient
    from infrastructure.http.wallet_client import WalletClient
logger = logging.getLogger("ReviewFetcher")

# Затримки між запитами на біржу (rate limiting)
RATE_LIMITS = {
    "Binance": 2.0,
    "Bybit": 1.5,
    "OKX": 1.5,
    "MEXC": 1.5,
    "CryptoBot": 1.5,
    "Wallet": 1.5,
}

# Пороги для автоматичного флагування
BAD_REVIEW_THRESHOLD_PCT = 15.0

# Базові ключові слова для швидкого pre-фільтру
_BASIC_BAD = [
    "scam", "шахрай", "кидало", "кинув", "розвів", "fraud",
    "fake", "не платить", "обманув", "обдурив", "кинули",
]

# Усі правила для цільових категорій (для швидкого фільтру)
_TARGET_CATEGORIES = {"TRIANGLE", "CASINO", "FINCRIME", "CHARGEBACK", "APPEAL_PRESSURE"}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _has_bad_keywords(text: str) -> bool:
    """
    Дворівневий фільтр:
    1. Швидкий пошук базових слів (str.contains)
    2. Regex по цільових категоріях
    """
    if not text:
        return False
    t = text.lower()
    if any(w in t for w in _BASIC_BAD):
        return True
    for rule in ALL_RULES:
        if rule.category in _TARGET_CATEGORIES:
            if rule.pattern.search(t):
                return True
    return False


def _analyze_review_text(text: str) -> dict:
    """
    Аналіз тексту відгуку через спільний матчер.

    Тут була власна копія проходу по `review_only` правилах — і ще одна,
    дослівна, у `core/analysis/review_analyzer.py`, яку не імпортував ніхто.
    Обидві не знали ні про заперечення, ні про SAFE-шар: відгук «мерчант не
    кидає, все чесно» рахувався нарівні зі скаргою.

    Тепер прохід один, із scope=reviews.
    """
    found = match_text(text, scope=SCOPE_REVIEWS)
    if not found.matches:
        return {"score": 0, "categories": [], "top_excerpt": ""}

    positive = [m for m in found.matches if m.weight > 0]
    if not positive:
        return {"score": 0, "categories": [], "top_excerpt": ""}

    return {
        "score": min(100, max(0, sum(m.weight for m in positive))),
        "categories": list(dict.fromkeys(m.category for m in positive)),
        "top_excerpt": positive[0].excerpt[:100],
    }


def _enrich_bad_text(text: str) -> dict:
    """Збагачує текст відгуку аналітикою для збереження в БД."""
    analysis = _analyze_review_text(text)
    return {
        "text": text[:300],
        "score": analysis["score"],
        "categories": analysis["categories"],
        "excerpt": analysis["top_excerpt"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# ReviewFetcher
# ─────────────────────────────────────────────────────────────────────────────

class ReviewFetcher:
    """
    Тягне відгуки мерчантів на вимогу (lazy), з-під RiskEngine.

    Історична довідка: тут була пріоритетна черга з фоновим воркером
    (schedule → _urgent_queue/_queue → _worker_loop → _fetch_and_save).
    Після переходу на lazy-модель сканер перестав викликати schedule(), тож
    воркер роками просто висів на порожній черзі, а _fetch_and_save —
    майже дослівна копія fetch_now — була недосяжна. Черга прибрана,
    лишився один шлях: fetch_now().
    """

    def __init__(
            self,
            db: MerchantDB,
            review_ttl_hours: float = 24.0,
            binance_client=None,
            bybit_client=None,
            okx_client=None,
            mexc_client=None,
            notifier=None,
    ):
        self._db = db
        self._review_ttl = review_ttl_hours
        self._notifier = notifier

        self._processed = 0
        self._errors = 0
        # Скільки фетчів прямо зараз у польоті — це те, що показує /status
        # замість колишнього розміру черги.
        self._in_flight = 0

        self._binance: Optional["BinanceClient"] = binance_client
        self._bybit: Optional["BybitP2PClient"] = bybit_client
        self._okx: Optional["OkxClient"] = okx_client
        self._mexc: Optional["MexcClient"] = None  # 🚀 ДОДАНО
        self._wallet: Optional["WalletClient"] = None
        self._cryptobot: Optional["CryptoBotWebClient"] = None
        self._exchange_fails: dict[str, int] = {"Binance": 0, "Bybit": 0, "OKX": 0, "MEXC": 0, "CryptoBot": 0}
        self._exchange_cooldown: dict[str, float] = {"Binance": 0.0, "Bybit": 0.0, "OKX": 0.0, "MEXC": 0.0,
                                                     "CryptoBot": 0.0}

    def bind_clients(self, binance=None, bybit=None, okx=None, mexc=None, cryptobot=None, wallet=None) -> None:
        if binance is not None: self._binance = binance
        if bybit is not None: self._bybit = bybit
        if okx is not None: self._okx = okx
        if mexc is not None: self._mexc = mexc
        if cryptobot is not None: self._cryptobot = cryptobot
        if wallet is not None: self._wallet = wallet
        logger.info(
            "ReviewFetcher clients bound: Binance=%s Bybit=%s OKX=%s MEXC=%s CryptoBot=%s Wallet=%s",
            "✅" if self._binance else "❌", "✅" if self._bybit else "❌", "✅" if self._okx else "❌",
            "✅" if self._mexc else "❌", "✅" if self._cryptobot else "❌", "✅" if self._wallet else "❌",
        )

    def bind_notifier(self, notifier) -> None:
        self._notifier = notifier

    @property
    def in_flight(self) -> int:
        """Скільки фетчів відгуків виконується просто зараз."""
        return self._in_flight

    async def start(self) -> None:
        logger.info(
            "ReviewFetcher готовий (lazy-режим) | ttl=%.1fh | clients: B=%s By=%s OKX=%s MEXC=%s CB=%s",
            self._review_ttl,
            "✅" if self._binance else "❌",
            "✅" if self._bybit else "❌",
            "✅" if self._okx else "❌",
            "✅" if self._mexc else "❌",
            "✅" if self._cryptobot else "❌",
        )

    async def stop(self) -> None:
        logger.info(
            "ReviewFetcher зупинено. Оброблено: %d, помилок: %d, у польоті: %d",
            self._processed, self._errors, self._in_flight,
        )

    async def _last_known(
            self, exchange: str, merchant_id: str, status: str, error_reason: str = "",
    ) -> dict:
        """
        Відповідь при невдалому фетчі: останні відомі відгуки + чесний статус.

        Раніше сюди поверталися нулі, і виклик у RiskEngine не міг відрізнити
        «відгуків немає» від «цього разу не дістали». Тепер лічильники й
        тексти беруться з бази (їх більше ніхто не стирає — див.
        `mark_reviews_unavailable`), а `status` каже, що свіжих даних немає.

        `data_at` у відповіді показує, наскільки старі ці дані. Нуль означає,
        що успішного збору не було жодного разу — тоді нулі справжні.
        """
        fallback = {
            "positive": 0, "negative": 0, "neutral": 0, "bad_texts": [],
            "status": status, "error_reason": error_reason, "data_at": 0,
        }
        try:
            stored = await self._db.get_reviews_summary(exchange, merchant_id)
        except Exception:
            return fallback
        if not stored:
            return fallback

        stored = dict(stored)
        stored["status"] = status
        stored["error_reason"] = error_reason
        return stored

    async def fetch_now(self, exchange: str, merchant_id: str) -> dict:
        """
        🚀 СИНХРОННИЙ ФЕТЧ: Викликається напряму з RiskEngine для нових мерчантів,
        щоб уникнути 'стану перегонів' (Race Condition), коли ордер аналізується швидше,
        ніж завантажаться його відгуки.
        """
        from state import state

        if not merchant_id:
            return {"positive": 0, "negative": 0, "neutral": 0, "bad_texts": [], "status": "UNKNOWN",
                    "error_reason": "empty merchant_id", "data_at": 0}

        # Нижче — три причини не ходити на біржу взагалі. У всіх трьох стан
        # бази не чіпаємо: це не нова інформація про мерчанта, а про нас.
        # Але й нулі не вигадуємо — віддаємо останнє відоме зі статусом,
        # який каже, що свіжого немає.
        if not state.stats.get("internet_connected", True):
            return await self._last_known(exchange, merchant_id, "UNAVAILABLE", "Internet is offline")

        # Degraded mode: після 3 підряд відмов біржа йде в cooldown, щоб не
        # довбати мертве API кожним циклом. Перенесено з _fetch_and_save —
        # раніше ця логіка жила тільки у мертвому шляху через чергу, тож
        # lazy-фетч довбав API без обмежень.
        if asyncio.get_event_loop().time() < self._exchange_cooldown.get(exchange, 0.0):
            logger.debug("fetch_now: %s у degraded mode, пропускаємо %s", exchange, merchant_id[:12])
            return await self._last_known(
                exchange, merchant_id, "UNAVAILABLE",
                f"{exchange} degraded mode (temporary cooldown)",
            )

        if exchange not in RATE_LIMITS:
            # Біржа взагалі не підтримується (не в RATE_LIMITS)
            logger.debug("fetch_now: %s не підтримує API відгуків [%s]", exchange, merchant_id[:12])
            reason = f"{exchange}: reviews API not supported"
            try:
                await self._db.mark_reviews_unavailable(
                    exchange, merchant_id, "NOT_SUPPORTED", reason,
                )
            except Exception:
                pass
            return await self._last_known(exchange, merchant_id, "NOT_SUPPORTED", reason)

        # ── Перевірка доступності ПЕРЕД запитом (per-exchange логіка) ──────
        _client_map = {
            "Binance": self._binance, "Bybit": self._bybit, "OKX": self._okx,
            "MEXC": self._mexc, "CryptoBot": self._cryptobot, "Wallet": self._wallet
        }
        client = _client_map.get(exchange)

        if exchange in ("CryptoBot", "Wallet"):
            if not client:
                return await self._last_known(
                    exchange, merchant_id, "NO_AUTH", f"{exchange} client is not initialized",
                )
            if not getattr(client, "userbot", None):
                await self._db.mark_reviews_unavailable(
                    exchange, merchant_id, "NO_SESSION", f"{exchange} userbot not set",
                )
                return await self._last_known(exchange, merchant_id, "NO_SESSION",
                                              f"{exchange} userbot not set")

        elif exchange == "MEXC":
            # MEXC: публічне API — тільки перевіряємо що клієнт є
            if not client:
                return await self._last_known(
                    exchange, merchant_id, "NO_AUTH", "MEXC client is not initialized",
                )

        elif exchange in ("Bybit", "Binance", "OKX"):
            # Bybit/Binance/OKX: всі три потребують браузерну сесію.
            # OKX використовує POST /v3/c2c/review/history (аналогічно Bybit/Binance).
            if not client:
                return await self._last_known(
                    exchange, merchant_id, "NO_AUTH", f"{exchange} client is not initialized",
                )
            session_h, _, _ = await self._db.get_auth_session(exchange)
            if not session_h:
                logger.debug("fetch_now: %s [%s] — немає перехопленої сесії", exchange, merchant_id[:12])
                reason = f"{exchange} browser session not captured"
                try:
                    await self._db.mark_reviews_unavailable(
                        exchange, merchant_id, "NO_SESSION", reason,
                    )
                except Exception:
                    pass
                return await self._last_known(exchange, merchant_id, "NO_SESSION", reason)

        else:
            # Невідома біржа з клієнтом — без перевірки
            pass

        self._in_flight += 1
        try:
            # У блок try-except виклику фетчерів додаємо роут:
            if exchange == "Binance":
                pos, neg, neutral, bad_texts = await self._fetch_binance(merchant_id)
            elif exchange == "Bybit":
                pos, neg, neutral, bad_texts = await self._fetch_bybit(merchant_id)
            elif exchange == "OKX":
                pos, neg, neutral, bad_texts = await self._fetch_okx(merchant_id)
            elif exchange == "MEXC":
                pos, neg, neutral, bad_texts = await self._fetch_mexc(merchant_id)
            elif exchange == "CryptoBot":
                pos, neg, neutral, bad_texts = await self._fetch_cryptobot(merchant_id)
            elif exchange == "Wallet":
                pos, neg, neutral, bad_texts = await self._fetch_wallet(merchant_id)

            self._exchange_fails[exchange] = 0
            self._processed += 1

            # Bybit/Binance/OKX/CryptoBot/Wallet: без сесії → NO_SESSION щоб needs_review_fetch
            # повернув True через 10 хв — як тільки сесія з'явиться, всі перефетчаться
            if exchange in ("Bybit", "Binance", "OKX", "CryptoBot", "Wallet"):
                if exchange in ("CryptoBot", "Wallet"):
                    save_status = "OK" if getattr(client, "userbot", None) else "NO_SESSION"
                else:
                    session_h, _, _ = await self._db.get_auth_session(exchange)
                    save_status = "OK" if session_h else "NO_SESSION"
            else:
                save_status = "OK"

            save_reason = ""
            if save_status == "OK" and (pos + neg + neutral) == 0 and not bad_texts:
                if exchange in ("Binance", "Bybit"):
                    pass  # For Binance/Bybit, 0 negative reviews is a normal successful result, NOT "no feedback"
                else:
                    save_status = "NO_FEEDBACK"
                    save_reason = f"{exchange} API returned 0 feedback entries"

            await self._db.save_reviews(
                exchange, merchant_id, pos, neg, neutral, bad_texts,
                status=save_status, error_reason=save_reason
            )

            total = pos + neg + neutral
            bad_pct = (neg / total * 100.0) if total > 0 else 0.0

            if save_status == "OK":
                try:
                    await self._db.save_review_snapshot(exchange, merchant_id, pos, neg, bad_pct)
                except Exception as _snap_err:
                    logger.debug("save_review_snapshot error: %s", _snap_err)

                if self._notifier is not None:
                    spawn(
                        self._notifier.redraw_alerts_for_merchant(exchange, merchant_id),
                        f"redraw-{exchange}-{merchant_id}",
                        logger_=logger,
                    )

            if bad_pct >= BAD_REVIEW_THRESHOLD_PCT and neg >= 3:
                logger.warning(
                    "🚨 Поганий мерчант %s [%s]: %.0f%% негативних (%d/%d)",
                    merchant_id, exchange, bad_pct, neg, total,
                )

            return {
                "positive": pos,
                "negative": neg,
                "neutral": neutral,
                "bad_texts": bad_texts,
                "status": save_status,
                "error_reason": save_reason,
                # Щойно зібрано — дані свіжі за визначенням.
                "data_at": time.time(),
            }
        except Exception as e:
            logger.warning(f"fetch_now помилка для {merchant_id}: {e}")
            self._errors += 1

            # 3 відмови поспіль → біржа в degraded mode на N хвилин.
            self._exchange_fails[exchange] = self._exchange_fails.get(exchange, 0) + 1
            if self._exchange_fails[exchange] >= 3:
                cooldown_sec = {"Binance": 300.0, "OKX": 600.0}.get(exchange, 7200.0)
                self._exchange_cooldown[exchange] = asyncio.get_event_loop().time() + cooldown_sec
                logger.error("🚨 %s API впало 3 рази! Degraded Mode на %.0f хв.", exchange, cooldown_sec / 60)

            emsg = str(e)
            status = "UNAVAILABLE"
            if "AuthError" in emsg:
                status = "SESSION_EXPIRED"
            elif "API_ERROR" in emsg:
                status = "API_ERROR"
            try:
                await self._db.mark_reviews_unavailable(
                    exchange, merchant_id, status, emsg[:500],
                )
            except Exception:
                pass
            return await self._last_known(exchange, merchant_id, status, emsg[:500])
        finally:
            self._in_flight -= 1

    # ─── Exchange fetchers ───────────────────────────────────────────────────

    def _send_burnout_alert(self, exchange: str):
        """Надсилає миттєве Telegram-сповіщення (і пише в лог) про згоряння сесії."""
        msg = f"❌ Ваша сесія <b>{exchange}</b> для парсингу відгуків згоріла.\n👉 Будь ласка, залогіньтесь знову (відскануйте QR-код)."
        logger.error(f"SESSION_BURNOUT:{exchange}: {msg}")

        # Відправляємо напряму через Telegram API, щоб не створювати циклічних імпортів з notifier
        try:
            from config import settings
            import aiohttp
            if settings.telegram_bot_token and settings.telegram_chat_id:
                async def _push():
                    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
                    payload = {"chat_id": settings.telegram_chat_id, "text": msg, "parse_mode": "HTML"}
                    try:
                        async with aiohttp.ClientSession() as s:
                            await s.post(url, json=payload, timeout=5)
                    except Exception as e:
                        logger.debug("burnout_alert push failed: %s", e)

                spawn(_push(), "burnout-alert-push", logger_=logger)
        except Exception:
            pass

    # ─── Exchange fetchers ───────────────────────────────────────────────────

    async def _fetch_binance(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        Binance: тексти негативних відгуків через перехоплену браузерну сесію.

        ПРИМІТКА: /bapi/c2c/v2/.../profile-and-ads і feedback-list повертають 404.
        Pos/neg COUNT розраховується в risk_engine через Order.positive_rate
        (доступний прямо в search response).

        Тексти відгуків (/v1/.../review/list-by-page) потребують повноцінну
        браузерну сесію (Csrftoken, BNC-Uuid, Device-Info, Fvideo).
        """
        client = self._binance
        if not client:
            return 0, 0, 0, []

        # Тексти відгуків — через перехоплену браузерну сесію
        headers, cookies, _ = await self._db.get_auth_session("Binance")
        if not headers:
            logger.debug("Binance [no session] %s: пропускаємо review texts", merchant_id)
            return 0, 0, 0, []

        try:
            raw_neg = await client.fetch_negative_reviews(
                merchant_id, rows=10,
                session_headers=headers,
                session_cookies=cookies,
            )
        except Exception as fe:
            if "AuthError" in str(fe):
                logger.error("🚨 Binance session burnout detected! %s", fe)
                spawn(self._db.invalidate_auth_session("Binance", user_id=0),
                      "invalidate-session-Binance", logger_=logger)
                self._send_burnout_alert("Binance")
                raise RuntimeError(f"AuthError: Binance session expired: {fe}")
            else:
                logger.debug("Binance review texts error %s: %s", merchant_id, fe)
                raise RuntimeError(f"API_ERROR: Binance review API failed: {fe}")

        bad_texts: list[dict] = []
        for item in raw_neg:
            content = str(item.get("comments") or item.get("content") or item.get("message") or "").strip()
            if content:
                enriched = _enrich_bad_text(content)
                enriched["keyword_flagged"] = _has_bad_keywords(content)
                bad_texts.append(enriched)

        neg = len(raw_neg)

        logger.debug("Binance %s: session bad_texts=%d (keyword_flagged=%d)", merchant_id, len(bad_texts),
                     sum(1 for t in bad_texts if t.get("keyword_flagged")))
        return 0, neg, 0, bad_texts

    async def _fetch_bybit(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        Bybit: тексти відгуків через перехоплену браузерну сесію.

        ПРИМІТКА: api2.bybit.com/fiat/otc/user/public/profile повертає 404.
        Pos/neg COUNT більше не доступний через API → підрахунок відбувається
        у risk_engine._async_analyze_inner через fallback з Order.finish_rate_pct.

        Цей метод отримує ТІЛЬКИ тексти негативних відгуків (bad_texts).
        """
        client = self._bybit
        if not client:
            return 0, 0, 0, []

        # Тексти відгуків — через перехоплену браузерну сесію
        headers, cookies, _ = await self._db.get_auth_session("Bybit")
        if not headers:
            logger.debug("Bybit [no session] %s: пропускаємо feedback", merchant_id)
            return 0, 0, 0, []

        try:
            raw_neg = await client.fetch_merchant_feedback(
                merchant_id,
                session_headers=headers,
                session_cookies=cookies
            )
        except Exception as fe:
            fe_str = str(fe)
            # ret_code=10007 = "User authentication failed" (Bybit) → сесія протухла
            is_auth_error = "AuthError" in fe_str or "10007" in fe_str or "authentication failed" in fe_str.lower()
            if is_auth_error:
                logger.error("🚨 Bybit session burnout detected! %s", fe)
                spawn(self._db.invalidate_auth_session("Bybit", user_id=0),
                      "invalidate-session-Bybit", logger_=logger)
                self._send_burnout_alert("Bybit")
                raise RuntimeError(f"AuthError: Bybit session expired: {fe}")
            else:
                logger.debug("Bybit feedback error %s: %s", merchant_id, fe)
                raise RuntimeError(f"API_ERROR: Bybit feedback API failed: {fe}")

        bad_texts: list[dict] = []
        for item in raw_neg:
            content = str(item.get("remark") or item.get("content") or "").strip()
            if content:
                enriched = _enrich_bad_text(content)
                enriched["keyword_flagged"] = _has_bad_keywords(content)
                bad_texts.append(enriched)

        # Якщо є погані тексти — беремо neg з них (pos/neg з профілю недоступний)
        neg = len(raw_neg) if raw_neg else 0

        logger.debug("Bybit %s: session bad_texts=%d (keyword_flagged=%d)", merchant_id, len(bad_texts),
                     sum(1 for t in bad_texts if t.get("keyword_flagged")))
        return 0, neg, 0, bad_texts

    async def _fetch_okx(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        OKX v3: браузерна сесія + POST /v3/c2c/review/history.

        Раніше використовувався /api/v5/c2c/order/user-feedback з API-ключами,
        але цей ендпоінт повертає 404 для P2P відгуків (доступний лише через браузер).

        Новий підхід (перехоплено через Playwright):
          - Endpoint: POST https://www.okx.com/v3/c2c/review/history
          - Payload: {currentPage, hasComment, pageSize, reviewFromBuyer,
                      reviewScoreType: "" | "negative" | "positive", pubUserId}
          - Auth: authorization JWT + cookies з браузерної сесії
        """
        import time
        from curl_cffi.requests import AsyncSession as CurlSession

        headers_dict, cookies_dict, _ = await self._db.get_auth_session("OKX")
        if not headers_dict and not cookies_dict:
            return 0, 0, 0, []

        headers_dict = {k.lower(): v for k, v in headers_dict.items()} if headers_dict else {}
        cookies_dict = cookies_dict or {}

        # Беремо лише потрібні заголовки з перехопленої сесії
        req_headers: dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "app-type": "web",
            "x-locale": "uk_UA",
            "origin": "https://www.okx.com",
            "referer": "https://www.okx.com/p2p-markets/uah/buy-usdt",
            "x-p2p-client": "web",
        }
        for key in ("authorization", "devid", "x-id-group", "x-site-info",
                    "user-agent", "x-client-signature", "x-client-signature-version"):
            val = headers_dict.get(key)
            if val:
                req_headers[key] = val

        if "authorization" not in req_headers:
            token_val = cookies_dict.get("token")
            if token_val:
                req_headers["authorization"] = f"Bearer {token_val}"

        from config import settings
        proxies = {"http": settings.proxy_url, "https": settings.proxy_url} if settings.proxy_url else None

        try:
            async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
                # Очищаємо дефолтні заголовки, які можуть дублюватись/конфліктувати
                session.headers.pop("User-Agent", None)
                session.headers.pop("user-agent", None)
                
                # Оновлюємо сесію поточними заголовками
                title_headers = {}
                for k, v in req_headers.items():
                    title_headers[k.title()] = v
                session.headers.update(title_headers)

                # 1) Загальна статистика: від покупців та від продавців
                ts = int(time.time() * 1000)
                url_all = f"https://www.okx.com/v3/c2c/review/history?t={ts}"

                payload_buyer = {
                    "currentPage": 1,
                    "hasComment": False,
                    "pageSize": 1,
                    "reviewFromBuyer": True,
                    "reviewScoreType": "",
                    "pubUserId": merchant_id,
                }
                payload_seller = {
                    "currentPage": 1,
                    "hasComment": False,
                    "pageSize": 1,
                    "reviewFromBuyer": False,
                    "reviewScoreType": "",
                    "pubUserId": merchant_id,
                }

                resp_buyer, resp_seller = await asyncio.gather(
                    session.post(url_all, json=payload_buyer, cookies=cookies_dict, timeout=10),
                    session.post(url_all, json=payload_seller, cookies=cookies_dict, timeout=10),
                    return_exceptions=True
                )

                if isinstance(resp_buyer, Exception):
                    raise resp_buyer
                if isinstance(resp_seller, Exception):
                    raise resp_seller

                if resp_buyer.status_code in (401, 403) or resp_seller.status_code in (401, 403):
                    raise RuntimeError(
                        f"AuthError: OKX session expired (HTTP {resp_buyer.status_code}/{resp_seller.status_code})")

                pos_buyer, neg_buyer = 0, 0
                pos_seller, neg_seller = 0, 0
                neutral_buyer, neutral_seller = 0, 0

                if resp_buyer.status_code == 200:
                    data_buyer = resp_buyer.json()
                    if data_buyer.get("code") == 0:
                        item_stats = data_buyer.get("data", {}).get("item", {})
                        pos_buyer = int(item_stats.get("positiveCount") or 0)
                        neg_buyer = int(item_stats.get("negativeCount") or 0)
                        all_buyer = int(item_stats.get("allCount") or 0)
                        neutral_buyer = max(0, all_buyer - pos_buyer - neg_buyer)
                    else:
                        raise RuntimeError(
                            f"API_ERROR: OKX buyer code={data_buyer.get('code')}, msg={data_buyer.get('msg', '')}")
                else:
                    raise RuntimeError(f"API_ERROR: OKX buyer history returned {resp_buyer.status_code}")

                if resp_seller.status_code == 200:
                    data_seller = resp_seller.json()
                    if data_seller.get("code") == 0:
                        item_stats = data_seller.get("data", {}).get("item", {})
                        pos_seller = int(item_stats.get("positiveCount") or 0)
                        neg_seller = int(item_stats.get("negativeCount") or 0)
                        all_seller = int(item_stats.get("allCount") or 0)
                        neutral_seller = max(0, all_seller - pos_seller - neg_seller)
                    else:
                        raise RuntimeError(
                            f"API_ERROR: OKX seller code={data_seller.get('code')}, msg={data_seller.get('msg', '')}")
                else:
                    raise RuntimeError(f"API_ERROR: OKX seller history returned {resp_seller.status_code}")

                pos = pos_buyer + pos_seller
                neg = neg_buyer + neg_seller
                neutral = neutral_buyer + neutral_seller

                # 2) Тексти негативних відгуків (reviewScoreType="negative")
                bad_texts: list[dict] = []

                # Завантаження негативних відгуків покупців
                if neg_buyer > 0:
                    neg_buyer_items = await self._fetch_okx_review_pages(
                        session, merchant_id, req_headers, cookies_dict,
                        score_type="negative", from_buyer=True, max_pages=3
                    )
                    for rev in neg_buyer_items:
                        comment_str = str(rev.get("comment") or "").strip()
                        reply_dict = rev.get("reviewReply") or {}
                        reply_str = str(reply_dict.get("comment") or "").strip() if isinstance(reply_dict, dict) else ""

                        parts = []
                        if comment_str:
                            parts.append(comment_str)
                        else:
                            parts.append("Покупець не залишив коментаря")

                        if reply_str:
                            parts.append(f"Відповідь мейкера: {reply_str}")

                        content = " | ".join(parts)
                        if comment_str or reply_str:
                            enriched = _enrich_bad_text(content)
                            enriched["keyword_flagged"] = _has_bad_keywords(content)
                            bad_texts.append(enriched)

                # Завантаження негативних відгуків продавців
                if neg_seller > 0:
                    neg_seller_items = await self._fetch_okx_review_pages(
                        session, merchant_id, req_headers, cookies_dict,
                        score_type="negative", from_buyer=False, max_pages=3
                    )
                    for rev in neg_seller_items:
                        comment_str = str(rev.get("comment") or "").strip()
                        reply_dict = rev.get("reviewReply") or {}
                        reply_str = str(reply_dict.get("comment") or "").strip() if isinstance(reply_dict, dict) else ""

                        parts = []
                        if comment_str:
                            parts.append(comment_str)
                        else:
                            parts.append("Продавець не залишив коментаря")

                        if reply_str:
                            parts.append(f"Відповідь мейкера: {reply_str}")

                        content = " | ".join(parts)
                        if comment_str or reply_str:
                            enriched = _enrich_bad_text(content)
                            enriched["keyword_flagged"] = _has_bad_keywords(content)
                            bad_texts.append(enriched)

                logger.debug(
                    "OKX [session] %s: pos=%d neg=%d neutral=%d | bad_texts=%d (keyword_flagged=%d)",
                    merchant_id, pos, neg, neutral, len(bad_texts),
                    sum(1 for t in bad_texts if t.get("keyword_flagged"))
                )
                return pos, neg, neutral, bad_texts

        except Exception as e:
            logger.debug("OKX review fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []

    async def _fetch_okx_review_pages(
            self,
            session,
            merchant_id: str,
            req_headers: dict,
            cookies_dict: dict,
            score_type: str,
            from_buyer: bool,
            max_pages: int = 3,
            page_size: int = 10,
    ) -> list[dict]:
        """
        Завантажує відгуки OKX з пагінацією через POST /v3/c2c/review/history.
        score_type: "" (всі) | "positive" | "negative"
        """
        import time
        all_items: list[dict] = []
        for page in range(1, max_pages + 1):
            ts = int(time.time() * 1000)
            url = f"https://www.okx.com/v3/c2c/review/history?t={ts}"
            payload = {
                "currentPage": page,
                "hasComment": False,
                "pageSize": page_size,
                "reviewFromBuyer": from_buyer,
                "reviewScoreType": score_type,
                "pubUserId": merchant_id,
            }
            try:
                resp = await session.post(
                    url, json=payload,
                    headers=req_headers, cookies=cookies_dict, timeout=10
                )
                if resp.status_code in (401, 403):
                    raise RuntimeError(f"AuthError: OKX session expired (HTTP {resp.status_code})")
                if resp.status_code != 200:
                    logger.debug("OKX review pages %s page=%d: status %d", merchant_id, page, resp.status_code)
                    break

                data = resp.json()
                if data.get("code") != 0:
                    logger.debug("OKX review pages %s: code=%s", merchant_id, data.get("code"))
                    break

                items = data.get("data", {}).get("item", {}).get("reviewHistoryDetail", []) or []
                all_items.extend(items)

                if len(items) < page_size:
                    break  # Остання сторінка

            except RuntimeError:
                raise
            except Exception as e:
                logger.debug("OKX review pages partial %s page=%d: %s", merchant_id, page, e)
                break

        return all_items

    async def fetch_okx_ad_detail(self, ad_id: str) -> dict:
        """Отримує умови ордеру OKX, використовуючи активну сесію з БД."""
        import time
        from curl_cffi.requests import AsyncSession as CurlSession

        headers_dict, cookies_dict, _ = await self._db.get_auth_session("OKX")
        if not headers_dict:
            return {}

        req_headers: dict[str, str] = {
            "accept": "application/json",
            "content-type": "application/json",
            "app-type": "web",
            "x-locale": "uk_UA",
        }
        for key in ("authorization", "devid", "x-id-group", "x-site-info",
                    "user-agent", "x-client-signature", "x-client-signature-version"):
            val = headers_dict.get(key)
            if val:
                req_headers[key] = val

        if "authorization" not in req_headers:
            return {}

        ts = int(time.time() * 1000)
        url = f"https://www.okx.com/v3/c2c/tradingOrders/getMarketplaceAdDetail?publicTradingOrderId={ad_id}&t={ts}"

        from config import settings
        proxies = {"http": settings.proxy_url, "https": settings.proxy_url} if settings.proxy_url else None

        try:
            async with CurlSession(impersonate="chrome124", proxies=proxies) as session:
                # Очищаємо дефолтні заголовки
                session.headers.pop("User-Agent", None)
                session.headers.pop("user-agent", None)
                
                # Оновлюємо сесію поточними заголовками
                title_headers = {}
                for k, v in req_headers.items():
                    title_headers[k.title()] = v
                session.headers.update(title_headers)
                
                resp = await session.get(url, cookies=cookies_dict, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("code") == 0:
                        return data.get("data", {}) or {}
        except Exception as e:
            logger.debug("Failed to fetch OKX ad detail for %s: %s", ad_id, e)
        return {}

    async def _fetch_mexc(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        client = self._mexc
        if not client:
            return 0, 0, 0, []

        try:
            # Один запит → і статистика, і тексти
            data = await client.fetch_merchant_reviews(merchant_id)
            pos = data.get("good", 0)
            neg = data.get("bad", 0)

            bad_texts: list[dict] = []
            for item in data.get("reviews", []):
                # MEXC поле: "comment"; rating=false → негативний відгук
                is_bad = item.get("rating") is False
                content = str(item.get("comment") or "").strip()
                if is_bad and content:
                    enriched = _enrich_bad_text(content)
                    enriched["keyword_flagged"] = _has_bad_keywords(content)
                    bad_texts.append(enriched)

            logger.debug("MEXC %s: pos=%d neg=%d bad_texts=%d (keyword_flagged=%d)", merchant_id, pos, neg,
                         len(bad_texts), sum(1 for t in bad_texts if t.get("keyword_flagged")))
            return pos, neg, 0, bad_texts

        except Exception as e:
            logger.debug("MEXC fetch error %s: %s", merchant_id, e)
            return 0, 0, 0, []

    async def _fetch_cryptobot(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        client = self._cryptobot
        if not client:
            return 0, 0, 0, []

        try:
            user_id = int(merchant_id)
            stats = await client.fetch_review_stats(user_id)
            pos = int(stats.get("positive", 0) or 0)
            neg = int(stats.get("negative", 0) or 0)
            total = int(stats.get("count", pos + neg) or (pos + neg))
            neutral = max(0, total - pos - neg)

            bad_texts: list[dict] = []
            if neg > 0:
                raw_neg = await client.fetch_negative_reviews(user_id)
                for item in raw_neg:
                    content = str(item.get("comment") or "").strip()
                    if not content:
                        continue
                    enriched = _enrich_bad_text(content)
                    enriched["keyword_flagged"] = _has_bad_keywords(content)
                    enriched["reviewer"] = item.get("name") or "Користувач"
                    enriched["payment_method"] = item.get("payment_method") or ""
                    enriched["date"] = item.get("date", "")
                    bad_texts.append(enriched)

            logger.debug("CryptoBot %s: pos=%d neg=%d neutral=%d bad_texts=%d",
                         merchant_id, pos, neg, neutral, len(bad_texts))
            return pos, neg, neutral, bad_texts

        except Exception as e:
            logger.debug("CryptoBot fetch error %s: %s", merchant_id, e)
            emsg = str(e)
            if "auth failed" in emsg.lower() or "unauthorized" in emsg.lower() or "401" in emsg:
                raise RuntimeError(f"AuthError: CryptoBot session expired: {e}")
            raise RuntimeError(f"API_ERROR: CryptoBot review fetch failed: {e}")

    async def _fetch_wallet(self, merchant_id: str) -> tuple[int, int, int, list[dict]]:
        """
        Wallet не має публічної текстової книги скарг/відгуків від користувачів.
        Вся репутація базується на суворому математичному відсотку виконання угод
        (successPercent), який уже дістається та перевіряється через стакан та
        детальний запит у RiskEngine.
        """
        logger.debug("Wallet %s: text reviews not supported natively, reputation uses completion metrics", merchant_id)
        return 0, 0, 0, []