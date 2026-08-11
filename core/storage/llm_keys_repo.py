# core/storage/llm_keys_repo.py
"""
Свої ключі до моделей: сховище й персональні вердикти.

Навіщо це взагалі. Спільний ключ означає спільний ліміт: Gemini Flash Lite
дає 500 запитів на добу, а 1290 мерчантів із тридобовим TTL вердикту з'їдають
~430 лише на оновлення. Другий активний користувач не подвоює витрати — він
ламає фічу обом, бо квота одна й обліку по людях немає. Питання не «хто
платить», а «в кого закінчиться».

Що тут важливо не переплутати. Вердикт від моделі — не факт, а судження:
воно змішує «що написано в умовах» (однакове для всіх) і «чи це прийнятно»
(у новачка й досвідченого різне). Спільним може бути тільки перше. Тому
персональні вердикти лежать окремою таблицею, а спільний `merchant_verdict`
лишається недоторканим — інакше перший користувач вирішував би, що побачать
решта.

Правило, яке діє з першого дня: **зламаний ключ не дає «OK»**. Немає ключа
або він відвалився — персонального вердикту просто немає, і людина бачить
базовий. Мовчазне «чисто» тут було б тим самим «не знаю ≠ безпечно», лише
на чужих ключах.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from core.security.crypto_utils import CryptoUtils

logger = logging.getLogger("LLMKeys")

# Провайдери в тому ж порядку, у якому їх пробує воркер.
PROVIDERS: tuple[str, ...] = ("groq", "gemini", "openai")

PROVIDER_TITLES = {
    "groq": "Groq",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
}

# Де взяти ключ — щоб не відповідати на це в чаті щоразу.
PROVIDER_HINTS = {
    "groq": "console.groq.com/keys",
    "gemini": "aistudio.google.com/apikey",
    "openai": "platform.openai.com/api-keys",
}

MODE_OFF = "off"
MODE_ONDEMAND = "ondemand"
MODE_ALWAYS = "always"
MODES = (MODE_OFF, MODE_ONDEMAND, MODE_ALWAYS)

MODE_TITLES = {
    MODE_OFF: "не використовувати",
    MODE_ONDEMAND: "на вимогу",
    MODE_ALWAYS: "завжди",
}

# Скільки живе персональний вердикт. Коротше за спільний (три доби): свій
# ключ на те й свій, щоб перевіряти частіше, коли людині це треба.
PERSONAL_TTL_HOURS = 24.0


def mask(api_key: str) -> str:
    """
    Показуємо рівно стільки, щоб людина впізнала свій ключ, і не більше.

    Показувати ключ цілком «бо це ж його власний» — найлегший спосіб
    залишити його в історії чату назавжди.
    """
    key = (api_key or "").strip()
    if len(key) <= 8:
        return "…" if key else ""
    return f"{key[:4]}…{key[-4:]}"


@dataclass(frozen=True, slots=True)
class LLMCredentials:
    """
    Чиї ключі йдуть із задачею.

    `owner_id == 0` означає спільні ключі проєкту з `.env` — саме вони
    працюють у всіх, хто своїх не додав.
    """
    owner_id: int = 0
    keys: dict[str, str] = field(default_factory=dict)

    def key_for(self, provider: str) -> str:
        return self.keys.get(provider, "")

    @property
    def is_personal(self) -> bool:
        return self.owner_id > 0 and bool(self.keys)

    def providers(self) -> tuple[str, ...]:
        return tuple(p for p in PROVIDERS if self.keys.get(p))

    def __repr__(self) -> str:
        # Ключі не мають потрапляти в лог навіть випадково, а `repr` —
        # найкоротший шлях туди: досить одного logger.debug("%s", task).
        return f"LLMCredentials(owner_id={self.owner_id}, providers={self.providers()})"

    __str__ = __repr__


SHARED = LLMCredentials()


class LLMKeysRepo:
    """Ключі користувачів і їхні персональні вердикти."""

    # ── Ключі ────────────────────────────────────────────────────────────

    async def save_llm_key(self, user_id: int, provider: str, api_key: str) -> tuple[bool, str]:
        """Зберігає ключ зашифрованим. Повертає (успіх, пояснення)."""
        provider = (provider or "").strip().lower()
        if provider not in PROVIDERS:
            return False, f"Невідомий провайдер: {provider}"
        key = (api_key or "").strip()
        if len(key) < 16:
            # Не перевірка формату, а захист від очевидного: сюди легко
            # прилітає обрізаний копіпаст або зайве слово з чату.
            return False, "Ключ надто короткий — схоже, скопіювався не повністю."

        now = time.time()
        await self._db.execute(
            """INSERT INTO user_llm_keys
                   (user_id, provider, api_key, enabled, created_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(user_id, provider) DO UPDATE SET
                   api_key = excluded.api_key,
                   enabled = 1,
                   updated_at = excluded.updated_at,
                   last_error = '',
                   last_error_at = 0""",
            (int(user_id), provider, CryptoUtils.encrypt(key), now, now),
        )
        await self._db.commit()
        logger.info("Ключ %s збережено для %s", provider, user_id)
        return True, f"{PROVIDER_TITLES[provider]} підключено."

    async def delete_llm_key(self, user_id: int, provider: str) -> bool:
        cur = await self._db.execute(
            "DELETE FROM user_llm_keys WHERE user_id = ? AND provider = ?",
            (int(user_id), (provider or "").strip().lower()),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_llm_keys(self, user_id: int) -> list[dict]:
        """
        Список ключів для показу: замаскований ключ і стан, без секрету.

        Розшифрований ключ звідси не виходить свідомо — це метод для UI, а
        не для викликів моделі.
        """
        async with self._db.execute(
            "SELECT * FROM user_llm_keys WHERE user_id = ? ORDER BY provider",
            (int(user_id),),
        ) as cur:
            rows = await cur.fetchall()

        out: list[dict] = []
        for row in rows:
            try:
                plain = CryptoUtils.decrypt(row["api_key"])
            except Exception:
                # Найчастіша причина — змінився ENCRYPTION_KEY. Мовчати не
                # можна: людина думає, що ключ підключений, а він мертвий.
                plain = ""
            out.append({
                "provider": row["provider"],
                "title": PROVIDER_TITLES.get(row["provider"], row["provider"]),
                "masked": mask(plain) if plain else "⚠️ не читається",
                "enabled": bool(row["enabled"]),
                "readable": bool(plain),
                "last_ok_at": float(row["last_ok_at"] or 0),
                "last_error": row["last_error"] or "",
                "last_error_at": float(row["last_error_at"] or 0),
            })
        return out

    async def credentials_for(self, user_id: int) -> LLMCredentials:
        """
        Ключі для виклику. Порожньо — якщо їх немає або режим вимкнений.

        Єдине місце, де ключ розшифровується. Далі він живе в пам'яті рівно
        стільки, скільки триває запит.
        """
        if not user_id or int(user_id) <= 0:
            return SHARED
        if await self.get_byok_mode(user_id) == MODE_OFF:
            return SHARED

        async with self._db.execute(
            "SELECT provider, api_key FROM user_llm_keys "
            "WHERE user_id = ? AND enabled = 1",
            (int(user_id),),
        ) as cur:
            rows = await cur.fetchall()

        keys: dict[str, str] = {}
        for row in rows:
            try:
                plain = CryptoUtils.decrypt(row["api_key"])
            except Exception as e:
                logger.warning("Ключ %s/%s не розшифровується: %s", user_id, row["provider"], e)
                continue
            if plain:
                keys[row["provider"]] = plain
        return LLMCredentials(owner_id=int(user_id), keys=keys) if keys else SHARED

    async def mark_key_ok(self, user_id: int, provider: str) -> None:
        await self._db.execute(
            "UPDATE user_llm_keys SET last_ok_at = ?, last_error = '', last_error_at = 0 "
            "WHERE user_id = ? AND provider = ?",
            (time.time(), int(user_id), provider),
        )
        await self._db.commit()

    async def mark_key_error(self, user_id: int, provider: str, error: str) -> None:
        """
        Записуємо ПРИЧИНУ, а не факт. «Ключ не працює» людина полагодити не
        може; «401 Unauthorized» і «429 quota exceeded» вимагають різного.
        """
        await self._db.execute(
            "UPDATE user_llm_keys SET last_error = ?, last_error_at = ? "
            "WHERE user_id = ? AND provider = ?",
            (str(error)[:200], time.time(), int(user_id), provider),
        )
        await self._db.commit()

    # ── Режим ────────────────────────────────────────────────────────────

    async def get_byok_mode(self, user_id: int) -> str:
        async with self._db.execute(
            "SELECT COALESCE(llm_byok_mode, 'ondemand') AS mode FROM scanner_users "
            "WHERE telegram_chat_id = ? OR user_id = ? LIMIT 1",
            (int(user_id), int(user_id)),
        ) as cur:
            row = await cur.fetchone()
        mode = (row["mode"] if row else MODE_ONDEMAND) or MODE_ONDEMAND
        return mode if mode in MODES else MODE_ONDEMAND

    async def set_byok_mode(self, user_id: int, mode: str) -> bool:
        if mode not in MODES:
            return False
        await self._db.execute(
            "UPDATE scanner_users SET llm_byok_mode = ? "
            "WHERE telegram_chat_id = ? OR user_id = ?",
            (mode, int(user_id), int(user_id)),
        )
        await self._db.commit()
        return True

    async def users_with_always_mode(self) -> list[int]:
        """Кому персональний аналіз потрібен без окремого прохання."""
        async with self._db.execute(
            "SELECT DISTINCT k.user_id FROM user_llm_keys k "
            "JOIN scanner_users u ON u.telegram_chat_id = k.user_id OR u.user_id = k.user_id "
            "WHERE k.enabled = 1 AND COALESCE(u.llm_byok_mode, 'ondemand') = 'always'"
        ) as cur:
            rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

    # ── Персональні вердикти ─────────────────────────────────────────────

    async def save_personal_verdict(
        self, user_id: int, exchange: str, merchant_id: str, terms_hash: str = "",
        verdict: str = "UNKNOWN", risk_type: str = "", reason: str = "",
        trade_recommendation: str = "PENDING", terms_summary: str = "",
        terms_facts: str = "", reviews_analysis: str = "", thought_process: str = "",
        source: str = "",
    ) -> None:
        await self._db.execute(
            """INSERT INTO merchant_verdict_user
                   (user_id, exchange, merchant_id, verdict, risk_type, reason,
                    trade_recommendation, terms_summary, terms_facts,
                    reviews_analysis, thought_process, terms_hash, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, exchange, merchant_id) DO UPDATE SET
                   verdict = excluded.verdict,
                   risk_type = excluded.risk_type,
                   reason = excluded.reason,
                   trade_recommendation = excluded.trade_recommendation,
                   terms_summary = excluded.terms_summary,
                   terms_facts = excluded.terms_facts,
                   reviews_analysis = excluded.reviews_analysis,
                   thought_process = excluded.thought_process,
                   terms_hash = excluded.terms_hash,
                   source = excluded.source,
                   updated_at = excluded.updated_at""",
            (
                int(user_id), exchange, merchant_id, verdict, risk_type, reason[:900],
                trade_recommendation, terms_summary[:300], terms_facts,
                reviews_analysis[:500], thought_process[:2000], terms_hash, source,
                time.time(),
            ),
        )
        await self._db.commit()

    async def get_personal_verdict(
        self, user_id: int, exchange: str, merchant_id: str, terms_hash: str = "",
    ) -> dict | None:
        """
        Персональний вердикт, якщо він свіжий і про ті самі умови.

        Розбіжність хешу означає, що мерчант переписав умови після того, як
        ми питали. Показувати старе судження про новий текст — саме той
        почерк, від якого чистився етап 0.
        """
        if not user_id or int(user_id) <= 0:
            return None
        async with self._db.execute(
            "SELECT * FROM merchant_verdict_user "
            "WHERE user_id = ? AND exchange = ? AND merchant_id = ?",
            (int(user_id), exchange, merchant_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        if terms_hash and row["terms_hash"] and row["terms_hash"] != terms_hash:
            return None
        age_h = (time.time() - float(row["updated_at"] or 0)) / 3600.0
        if age_h > PERSONAL_TTL_HOURS:
            return None
        # UNKNOWN зберігається (щоб не молотити зламаний ключ по колу), але
        # назовні не віддається: «не знаю» не має підмінювати базовий
        # вердикт, який у людини вже є.
        if (row["verdict"] or "UNKNOWN").upper() == "UNKNOWN":
            return None
        return {k: row[k] for k in row.keys()}

    async def drop_personal_verdicts(self, user_id: int) -> int:
        cur = await self._db.execute(
            "DELETE FROM merchant_verdict_user WHERE user_id = ?", (int(user_id),)
        )
        await self._db.commit()
        return cur.rowcount
