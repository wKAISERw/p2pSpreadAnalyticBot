# core/storage/risk_repo.py
"""
Персональні налаштування ріск-енджину: профіль, політики, власні сигнали.

Лежить окремо від `merchant_repo` навмисно. Там факти про мерчанта —
глобальні, спільні для всіх, на ключі `(exchange, merchant_id)`. Тут
рішення людини про те, що з тими фактами робити, — і вони персональні за
визначенням.

Порожня таблиця означає «нічого не налаштовано», і резолвер падає на
дефолти сигналу. Тобто міграція нікому нічого не змінює, поки людина сама
не зайде в налаштування.
"""
from __future__ import annotations

import json
import logging
import time

from core.risk.policy import ACTIONS, PROFILES, PROFILE_BALANCED, SignalPolicy
from core.risk.signals import (
    LAYER_HARD, LAYER_SAFE, LAYER_SOFT, LAYER_WARN,
    SCOPE_BOTH, SCOPE_REVIEWS, SCOPE_TERMS,
    Signal, compile_phrases,
)

logger = logging.getLogger("RiskRepo")

_LAYERS = (LAYER_HARD, LAYER_SOFT, LAYER_WARN, LAYER_SAFE)
_SCOPES = (SCOPE_TERMS, SCOPE_REVIEWS, SCOPE_BOTH)

# Скільки власних сигналів дозволено одному користувачеві.
#
# Не через місце в базі, а через час: кожен сигнал — це ще один регекс на
# кожен текст кожного ордера. Двісті власних правил перетворили б цикл
# сканера на перебір.
MAX_USER_SIGNALS = 50


class RiskRepo:
    """Профіль, політики й власні сигнали користувача."""

    # ── Профіль ──────────────────────────────────────────────────────────

    async def get_risk_profile(self, user_id: int) -> str:
        async with self._db.execute(
            "SELECT risk_profile FROM scanner_users WHERE user_id = ?", (int(user_id),)
        ) as cur:
            row = await cur.fetchone()
        value = (row["risk_profile"] if row else "") or PROFILE_BALANCED
        return value if value in PROFILES else PROFILE_BALANCED

    async def set_risk_profile(self, user_id: int, profile: str) -> bool:
        if profile not in PROFILES:
            return False
        await self._db.execute(
            "UPDATE scanner_users SET risk_profile = ? WHERE user_id = ?",
            (profile, int(user_id)),
        )
        await self._db.commit()
        return True

    # ── Політики ─────────────────────────────────────────────────────────

    async def get_policies(self, user_id: int) -> dict[str, SignalPolicy]:
        """Точкові налаштування користувача. Порожньо — значить, дефолти."""
        async with self._db.execute(
            "SELECT signal_key, enabled, on_buy, on_sell, weight_override "
            "FROM risk_policies WHERE user_id = ?",
            (int(user_id),),
        ) as cur:
            rows = await cur.fetchall()

        out: dict[str, SignalPolicy] = {}
        for row in rows:
            out[row["signal_key"]] = SignalPolicy(
                signal_key=row["signal_key"],
                enabled=bool(row["enabled"]),
                on_buy=row["on_buy"],
                on_sell=row["on_sell"],
                weight_override=row["weight_override"],
            )
        return out

    async def set_policy(self, user_id: int, policy: SignalPolicy) -> bool:
        if policy.on_buy not in ACTIONS or policy.on_sell not in ACTIONS:
            logger.warning("Невідома дія в політиці %s: %s/%s",
                           policy.signal_key, policy.on_buy, policy.on_sell)
            return False
        await self._db.execute(
            """
            INSERT INTO risk_policies
                (user_id, signal_key, enabled, on_buy, on_sell, weight_override, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, signal_key) DO UPDATE SET
                enabled         = excluded.enabled,
                on_buy          = excluded.on_buy,
                on_sell         = excluded.on_sell,
                weight_override = excluded.weight_override,
                updated_at      = excluded.updated_at
            """,
            (int(user_id), policy.signal_key, int(policy.enabled),
             policy.on_buy, policy.on_sell, policy.weight_override, time.time()),
        )
        await self._db.commit()
        return True

    async def reset_policy(self, user_id: int, signal_key: str) -> bool:
        """
        Прибирає точкове налаштування — сигнал повертається до дефолту.

        Саме видалення, а не запис дефолтних значень: інакше зміна дефолту
        в коді не дійшла б до тих, хто колись «скинув» налаштування.
        """
        cur = await self._db.execute(
            "DELETE FROM risk_policies WHERE user_id = ? AND signal_key = ?",
            (int(user_id), signal_key),
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def reset_all_policies(self, user_id: int) -> int:
        cur = await self._db.execute(
            "DELETE FROM risk_policies WHERE user_id = ?", (int(user_id),)
        )
        await self._db.commit()
        return cur.rowcount

    # ── Власні сигнали ───────────────────────────────────────────────────

    async def get_user_signals(self, user_id: int) -> list[Signal]:
        """
        Сигнали, які людина додала сама, вже скомпільовані.

        Зіпсовані рядки пропускаємо з попередженням, а не валимо весь набір:
        одне криве правило не має позбавляти людину решти захисту.
        """
        async with self._db.execute(
            "SELECT * FROM risk_user_signals WHERE user_id = ? AND enabled = 1",
            (int(user_id),),
        ) as cur:
            rows = await cur.fetchall()

        out: list[Signal] = []
        for row in rows:
            try:
                phrases = json.loads(row["phrases_json"] or "[]")
                negations = json.loads(row["negations_json"] or "[]")
            except Exception as e:
                logger.warning("Сигнал %s/%s: зіпсований JSON (%s)", user_id, row["key"], e)
                continue

            pattern = compile_phrases(phrases)
            if pattern is None:
                # Сигнал без фраз збігався б із будь-чим — це не «ловить
                # усе», це «ловить порожнечу». Мовчки пропускаємо.
                logger.warning("Сигнал %s/%s без фраз — пропущено", user_id, row["key"])
                continue

            out.append(Signal(
                key=row["key"],
                category=row["category"],
                title=row["title"],
                pattern=pattern,
                layer=row["layer"] if row["layer"] in _LAYERS else LAYER_SOFT,
                weight=int(row["weight"] or 0),
                scope=row["scope"] if row["scope"] in _SCOPES else SCOPE_TERMS,
                why=row["why"] or "",
                negations=tuple(str(n) for n in negations if str(n).strip()),
                owner=f"user:{user_id}",
            ))
        return out

    async def save_user_signal(
        self,
        user_id: int,
        key: str,
        title: str,
        phrases: list[str],
        category: str = "CUSTOM",
        negations: list[str] | None = None,
        layer: str = LAYER_SOFT,
        weight: int = 30,
        scope: str = SCOPE_TERMS,
        why: str = "",
    ) -> tuple[bool, str]:
        """
        Створює або оновлює власний сигнал. Повертає (успіх, пояснення).

        Перевірки тут, а не в UI: до цієї функції прийде ще й API дашборда,
        і дублювати правила в двох місцях означає рано чи пізно їх розвести.
        """
        clean = [p.strip() for p in (phrases or []) if p and p.strip()]
        if not clean:
            return False, "Потрібна хоча б одна фраза."
        if compile_phrases(clean) is None:
            return False, "З цих фраз не виходить жодного шаблону."
        if layer not in _LAYERS:
            return False, f"Невідомий шар: {layer}"
        if scope not in _SCOPES:
            return False, f"Невідома область: {scope}"

        async with self._db.execute(
            "SELECT COUNT(*) AS n FROM risk_user_signals WHERE user_id = ? AND key != ?",
            (int(user_id), key),
        ) as cur:
            existing = (await cur.fetchone())["n"]
        if existing >= MAX_USER_SIGNALS:
            return False, f"Більше {MAX_USER_SIGNALS} власних сигналів — забагато для швидкого сканування."

        await self._db.execute(
            """
            INSERT INTO risk_user_signals
                (user_id, key, category, title, phrases_json, negations_json,
                 layer, weight, scope, why, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id, key) DO UPDATE SET
                category       = excluded.category,
                title          = excluded.title,
                phrases_json   = excluded.phrases_json,
                negations_json = excluded.negations_json,
                layer          = excluded.layer,
                weight         = excluded.weight,
                scope          = excluded.scope,
                why            = excluded.why
            """,
            (int(user_id), key, category, title,
             json.dumps(clean, ensure_ascii=False),
             json.dumps(negations or [], ensure_ascii=False),
             layer, int(weight), scope, why, time.time()),
        )
        await self._db.commit()
        return True, "Збережено."

    async def delete_user_signal(self, user_id: int, key: str) -> bool:
        cur = await self._db.execute(
            "DELETE FROM risk_user_signals WHERE user_id = ? AND key = ?",
            (int(user_id), key),
        )
        await self._db.execute(
            "DELETE FROM risk_policies WHERE user_id = ? AND signal_key = ?",
            (int(user_id), key),
        )
        await self._db.commit()
        return cur.rowcount > 0

    # ── Усе разом ────────────────────────────────────────────────────────

    async def resolver_for(self, user_id: int):
        """
        Готовий `PolicyResolver` під користувача.

        Одне місце, яке знає, як зібрати профіль і політики докупи, — щоб
        кожен викликач не збирав їх по-своєму.
        """
        from core.risk.policy import PolicyResolver

        return PolicyResolver(
            profile=await self.get_risk_profile(user_id),
            overrides=await self.get_policies(user_id),
        )
