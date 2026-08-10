# core/engine/readiness.py
"""
Перевірка готовності тейкер-режиму: що завадить, ще до першого алерта.

Досі всі ці перевірки жили в циклі сканера й спрацьовували вже після
запуску. Людина налаштовувала режим, тиснула «Запустити» і чекала — а
причина, з якої алерти не приходили, лежала в налаштуваннях і була видна
одразу: обрано банки, карток яких немає; обсяг більший за все, що є на
картках; місячна межа банку вже майже вибрана.

Порядок перевірок тут відповідає порядку, у якому їх робить движок, тому
попередження називає ту саму причину, що потім прилетить у
`/card_rejections`. Розбіжність між «майстер сказав ок» і «сканер мовчить»
була б гіршою за відсутність перевірки взагалі.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from config.banks import bank_display_name, get_bank_profile, normalize_bank
from config.card_limits import (
    FEATURE_IGNORE_MERCHANT_BANKS, SPLIT_OFF, default_limits_for_bank,
)
from core.engine.bank_scope import resolve_banks

logger = logging.getLogger(__name__)

# Рівні: blocker — алертів не буде взагалі; warning — буде, але не так, як
# людина очікує; note — просто варто знати.
BLOCKER = "blocker"
WARNING = "warning"
NOTE = "note"

_ICONS = {BLOCKER: "🛑", WARNING: "⚠️", NOTE: "💡"}


@dataclass
class Check:
    level: str
    text: str
    hint: str = ""

    def render(self) -> str:
        line = f"{_ICONS.get(self.level, '•')} {self.text}"
        if self.hint:
            line += f"\n   <i>{self.hint}</i>"
        return line


def _uah(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₴"


async def check_taker_readiness(db, user: dict, mode: str) -> list[Check]:
    """
    Що завадить режиму працювати так, як людина щойно налаштувала.

    Порожній список означає «все сходиться». Виняток усередині ковтати не
    можна мовчки, але й падати не варто: перевірка — допоміжна, а не умова
    запуску.
    """
    if mode not in ("TAKER_BUY", "TAKER_SELL"):
        return []

    checks: list[Check] = []
    user_id = user.get("user_id")
    if not db or not user_id:
        return checks

    is_buy = mode == "TAKER_BUY"
    direction = "buy" if is_buy else "sell"

    card_settings = await db.get_user_card_settings(user_id) or {}
    module_on = card_settings.get("card_module_mode") != "off"
    single_modes_on = bool(card_settings.get("enable_in_single_modes"))

    # Стан експериментальних фіч читаємо тут, бо від них залежить і стеля
    # капіталу, і формулювання підказок. Перевірка, яка їх не знає, казала б
    # користувачу неправду про його ж налаштування.
    try:
        ignore_merchant_banks = await db.get_feature_status(
            user_id, FEATURE_IGNORE_MERCHANT_BANKS)
    except Exception as e:
        logger.debug("Не вдалось прочитати стан фіч карток: %s", e)
        ignore_merchant_banks = False

    selected_banks = [normalize_bank(b) for b in (resolve_banks(user, mode, direction) or [])]
    cards = await db.get_cards(user_id, status="active")
    card_banks = {normalize_bank(c.get("bank_name", "")) for c in cards}

    # ── 1. Картковий модуль без карток ────────────────────────────────────
    if module_on and single_modes_on and not cards:
        checks.append(Check(
            BLOCKER,
            "Картковий модуль увімкнено для одиночних режимів, але активних карток немає",
            "Жоден ордер не пройде перевірку. Додайте картку через /cards "
            "або вимкніть «В окремих режимах» у налаштуваннях карток.",
        ))
        return checks

    if not module_on or not single_modes_on:
        # Без карткового модуля решта перевірок безпредметна: движок карток
        # не питає, і ліміти ні на що не впливають.
        if selected_banks:
            checks.append(Check(
                NOTE,
                "Картковий модуль не бере участі в цьому режимі",
                "Баланси й ліміти карток не перевірятимуться — алерти "
                "приходитимуть за самою лише ціною.",
            ))
        return checks

    # ── 2. Банки, карток яких немає ───────────────────────────────────────
    #
    # З увімкненим «Ігнорувати фільтр банків мерчанта» це вже не блокер:
    # ордер такого банку пройде на інших картках, просто з позначкою
    # «узгодити в чаті». Лишати тут 🛑 означало б лякати тим, чого немає.
    if selected_banks:
        missing = [b for b in selected_banks if b not in card_banks]
        if len(missing) == len(selected_banks):
            if ignore_merchant_banks and card_banks:
                checks.append(Check(
                    NOTE,
                    "Карток обраних банків немає, але фільтр банків мерчанта вимкнено",
                    f"Ордери підбиратимуться на ваші картки "
                    f"({', '.join(bank_display_name(b) for b in sorted(card_banks))}), "
                    f"і бот нагадає спитати мерчанта, чи приймає він переказ звідти.",
                ))
            else:
                checks.append(Check(
                    BLOCKER,
                    "Жоден з обраних банків не має активної картки",
                    f"Обрано: {', '.join(bank_display_name(b) for b in selected_banks)}. "
                    f"Мерчант приймає оплату лише своїм банком, тож такі ордери "
                    f"відсіюватимуться всі до одного.",
                ))
        elif missing and not ignore_merchant_banks:
            checks.append(Check(
                WARNING,
                f"Немає карток для {len(missing)} з обраних банків: "
                + ", ".join(bank_display_name(b) for b in missing),
                "Ордери цих банків проходити не будуть.",
            ))

    usable_banks = [b for b in selected_banks if b in card_banks] or sorted(card_banks)
    if not usable_banks:
        return checks

    # ── 3. Обсяг проти реального капіталу ─────────────────────────────────
    if is_buy:
        desired = float(user.get("taker_buy_amount", 0) or 0)
        price = float(user.get("taker_buy_max_price", 0) or 0)
        if desired > 0 and price > 0:
            needed = desired * price
            breakdown = await db.get_user_capital_breakdown(user_id, allowed_banks=usable_banks)
            usable = float(breakdown.get("usable", 0.0))
            total = float(breakdown.get("total", 0.0))

            inter_bank = bool(breakdown.get("inter_bank"))

            if usable < needed:
                # Стеля залежить від того, чи вміє движок збирати суму з
                # різних банків. Доти цей текст стверджував «не вміє»
                # беззастережно — і після вмикання фічі казав користувачу
                # неправду про його ж налаштування.
                if inter_bank:
                    hint = (
                        f"Потрібно {_uah(needed)}, а на всіх картках разом "
                        f"{_uah(total)}"
                    )
                    if not ignore_merchant_banks:
                        hint += (
                            ". Кошик між банками увімкнено, але він лишається в "
                            "межах банків, які мерчант вказав в оголошенні — "
                            "увімкніть «Ігнорувати фільтр банків мерчанта», щоб "
                            "використати всі картки"
                        )
                else:
                    hint = (
                        f"Потрібно {_uah(needed)}, доступно в одному банку "
                        f"{_uah(usable)}"
                    )
                    if total > usable + 1:
                        hint += (
                            f". Разом на картках {_uah(total)}, але зібрати їх в "
                            f"одну угоду движок поки не вміє — гроші в різних "
                            f"банках. Вмикається в /features → 💳 КАРТКИ"
                        )
                mode_now = user.get("buy_balance_mode", "CARD_ENFORCED")
                if mode_now == "AUTO_SCALE":
                    hint += ". Авто-масштабування підлаштує обсяг під наявне"
                    level = NOTE
                else:
                    hint += (
                        ". Увімкніть авто-масштабування або зменште обсяг, "
                        "інакше ордери відсіюватимуться"
                    )
                    level = WARNING
                checks.append(Check(level, "Обсягу купівлі не вистачає на картках", hint))

    # ── 4. Обсяг проти безпечної місячної межі довідника ───────────────────
    #
    # Тут перевірка не про «не вийде», а про «вийде, але картку заблокують».
    # Довідник дає рекомендовану місячну межу; сканер її не порушить, бо
    # ліміт стоїть у ефективних лімітах картки — але людина має розуміти,
    # чому обсяг упреться в стелю раніше, ніж скінчаться гроші.
    for bank in usable_banks:
        profile = get_bank_profile(bank)
        if not profile.safe_monthly_uah:
            continue
        limits = default_limits_for_bank(bank)
        monthly_cap = limits[f"monthly_{'out' if is_buy else 'in'}_max"]

        used = 0.0
        for card in cards:
            if normalize_bank(card.get("bank_name", "")) != bank:
                continue
            used += await db.get_monthly_used(card["id"], "out" if is_buy else "in")

        if monthly_cap > 0 and used >= monthly_cap * 0.8:
            checks.append(Check(
                WARNING,
                f"{bank_display_name(bank)}: місячний оборот майже вичерпано "
                f"({_uah(used)} з {_uah(monthly_cap)})",
                "Це межа з довідника банків, а не банківський ліміт. "
                "Змінити — у /set_bank_limits.",
            ))

    # ── 5. Налаштування, які самі себе обмежують ──────────────────────────
    if card_settings.get("card_split_mode") == SPLIT_OFF:
        checks.append(Check(
            NOTE,
            "Спліт карток вимкнено",
            "Підійдуть лише ордери, які покриває одна картка одним переказом.",
        ))

    return checks


def render_checks(checks: list[Check], title: str = "Перевірка налаштувань") -> str:
    """Готовий блок для повідомлення. Порожньо — коли все сходиться."""
    if not checks:
        return ""
    lines = [f"<b>{title}</b>"]
    for level in (BLOCKER, WARNING, NOTE):
        lines.extend(c.render() for c in checks if c.level == level)
    return "\n".join(lines)


def has_blockers(checks: list[Check]) -> bool:
    return any(c.level == BLOCKER for c in checks)


async def readiness_block(db, user_id: int, mode: str) -> str:
    """
    Готовий блок попереджень для повідомлення бота, або порожній рядок.

    Живе тут, а не в хендлерах: його кличуть і майстер, і швидкий старт, і
    екран пресету. Копія в кожному з них розійшлась би — цей проєкт уже
    п'ять разів на цьому обпікся.
    """
    if not db or not user_id:
        return ""
    try:
        user = await db.get_user_by_id(user_id)
        if not user:
            return ""
        block = render_checks(await check_taker_readiness(db, user, mode))
        return f"\n\n{block}" if block else ""
    except Exception as e:
        # Перевірка допоміжна: якщо вона впала, режим усе одно має запуститись.
        logger.debug("Перевірка готовності %s не виконалась: %s", mode, e)
        return ""
