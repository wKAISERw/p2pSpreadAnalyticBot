# bot/handlers/byok.py
"""
Свої ключі до моделей — підключення, режим і перевірка на вимогу.

Навіщо це є. Спільний ключ означає спільний ліміт: квота одна, обліку по
людях немає, і другий активний користувач не подвоює витрати, а ламає фічу
обом. Свій ключ знімає це — і дає персональну інференцію, яка на спільному
кеші неможлива за визначенням.

Три речі, які тут навмисно зроблені саме так.

**Повідомлення з ключем видаляється одразу.** Інакше він назавжди лишається
в історії чату — місці, яке людина не вважає сховищем секретів і куди
заглядають через плече.

**Ключ ніде не показується цілком.** Ані після збереження, ані в списку.
Замаскованого досить, щоб упізнати свій.

**Без ключа нічого не ламається.** Базовий вердикт лишається той самий, що
й був: підписка дає працюючий движок, ключ додає персональну перевірку
понад нього. Модель стає нечесною рівно тоді, коли без ключа перестає
працювати базове.
"""
from __future__ import annotations

import logging
import time
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.handlers.core import _db, _llm_pool
from bot.keyboards.byok import byok_key_kb, byok_main_kb, byok_mode_kb
from core.storage.llm_keys_repo import (
    MODE_ALWAYS, MODE_OFF, MODE_ONDEMAND, MODE_TITLES, PROVIDER_HINTS,
    PROVIDER_TITLES, PROVIDERS,
)

router = Router()
logger = logging.getLogger("BYOK")


class BYOKStates(StatesGroup):
    waiting_key = State()


def _ago(ts: float) -> str:
    if not ts:
        return "ще не пробували"
    mins = max(0, int((time.time() - ts) / 60))
    if mins < 60:
        return f"{mins} хв тому"
    if mins < 60 * 24:
        return f"{mins // 60} год тому"
    return f"{mins // (60 * 24)} дн тому"


async def _main_text(user_id: int) -> tuple[str, list[dict], str]:
    keys = await _db.list_llm_keys(user_id)
    mode = await _db.get_byok_mode(user_id)

    lines = [
        "🔑 <b>Свої ключі до AI</b>",
        "",
        "Базовий вердикт працює завжди й без ключа — він однаковий для всіх, "
        "бо описує те, що написано в умовах мерчанта.",
        "",
        "Свій ключ дає <b>персональну</b> перевірку: окрему чергу, окремий "
        "ліміт і оцінку саме для тебе. Чужий 429 на неї не впливає.",
    ]
    if keys:
        lines.append("")
        for row in keys:
            if not row["readable"]:
                lines.append(
                    f"⚠️ <b>{row['title']}</b>: ключ не читається — "
                    f"схоже, змінився ENCRYPTION_KEY. Підключи заново."
                )
            elif row["last_error"]:
                lines.append(
                    f"❗ <b>{row['title']}</b> <code>{row['masked']}</code> — "
                    f"остання помилка: {row['last_error']} ({_ago(row['last_error_at'])})"
                )
            else:
                lines.append(
                    f"✅ <b>{row['title']}</b> <code>{row['masked']}</code> — "
                    f"успішно {_ago(row['last_ok_at'])}"
                )
        lines.append("")
        lines.append(f"Режим: <b>{MODE_TITLES.get(mode, mode)}</b>.")
    else:
        lines.append("")
        lines.append("<i>Ключів не підключено — працює базовий вердикт.</i>")

    lines.append("")
    lines.append("⚠️ Аналіз робить AI-модель і може помилятись. Рішення за тобою.")
    return "\n".join(lines), keys, mode


@router.callback_query(F.data == "byok:main")
async def on_byok_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, keys, mode = await _main_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=byok_main_kb(keys, mode))
    await call.answer()


@router.callback_query(F.data.startswith("byok:add:"))
async def on_byok_add(call: CallbackQuery, state: FSMContext) -> None:
    provider = call.data.split(":")[-1]
    if provider not in PROVIDERS:
        return await call.answer("Невідомий провайдер", show_alert=True)
    await state.update_data(provider=provider)
    await state.set_state(BYOKStates.waiting_key)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🔑 <b>{PROVIDER_TITLES[provider]}</b>\n\n"
            f"Надішли ключ одним повідомленням.\n"
            f"Взяти можна тут: <code>{PROVIDER_HINTS[provider]}</code>\n\n"
            f"<i>Повідомлення з ключем я одразу видалю, а сам ключ збережу "
            f"зашифрованим. Показуватись він більше ніде не буде.</i>"
        )
    await call.answer()


@router.message(BYOKStates.waiting_key)
async def on_byok_key(message: Message, state: FSMContext) -> None:
    key = (message.text or "").strip()
    # Спершу прибираємо повідомлення, потім усе інше. Якщо далі щось піде
    # не так, ключ уже не лежить у чаті.
    with suppress(Exception):
        await message.delete()

    data = await state.get_data()
    provider = data.get("provider", "")
    await state.clear()

    ok, note = await _db.save_llm_key(message.from_user.id, provider, key)
    if not ok:
        return await message.answer(f"❌ {note}")

    text, keys, mode = await _main_text(message.from_user.id)
    await message.answer(f"✅ {note}\n\n{text}", reply_markup=byok_main_kb(keys, mode))


@router.callback_query(F.data.startswith("byok:key:"))
async def on_byok_key_menu(call: CallbackQuery) -> None:
    provider = call.data.split(":")[-1]
    if provider not in PROVIDERS:
        return await call.answer("Невідомий провайдер", show_alert=True)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"🔑 <b>{PROVIDER_TITLES[provider]}</b>\n\n"
            f"Що зробити з цим ключем?",
            reply_markup=byok_key_kb(provider),
        )
    await call.answer()


@router.callback_query(F.data.startswith("byok:del:"))
async def on_byok_delete(call: CallbackQuery) -> None:
    provider = call.data.split(":")[-1]
    await _db.delete_llm_key(call.from_user.id, provider)
    # Персональні вердикти теж прибираємо: вони зроблені ключем, якого вже
    # немає, і показувати їх далі означало б видавати чуже за поточне.
    dropped = await _db.drop_personal_verdicts(call.from_user.id)
    text, keys, mode = await _main_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=byok_main_kb(keys, mode))
    await call.answer(
        f"Ключ видалено, персональних вердиктів прибрано: {dropped}", show_alert=True
    )


@router.callback_query(F.data == "byok:mode")
async def on_byok_mode_menu(call: CallbackQuery) -> None:
    mode = await _db.get_byok_mode(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "⚙️ <b>Коли витрачати твій ключ</b>\n\n"
            "🚫 <b>не використовувати</b> — ключ лежить, але не працює.\n"
            "👆 <b>на вимогу</b> — тільки коли натиснеш «перевірити моїм ключем» "
            "під алертом.\n"
            "♾ <b>завжди</b> — на кожного мерчанта, якого тобі показують. "
            "Найточніше й найдорожче: ліміт твій.\n\n"
            "<i>Базовий вердикт працює в будь-якому режимі.</i>",
            reply_markup=byok_mode_kb(mode),
        )
    await call.answer()


@router.callback_query(F.data.startswith("byok:mode:"))
async def on_byok_mode_set(call: CallbackQuery) -> None:
    mode = call.data.split(":")[-1]
    if not await _db.set_byok_mode(call.from_user.id, mode):
        return await call.answer("Невідомий режим", show_alert=True)
    text, keys, current = await _main_text(call.from_user.id)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=byok_main_kb(keys, current))
    await call.answer(f"Режим: {MODE_TITLES[mode]}")


# ── Перевірка на вимогу ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("aicheck:"))
async def on_ai_check(call: CallbackQuery) -> None:
    """
    «Перевірити моїм ключем» під алертом.

    Свідомо не блокує інтерфейс очікуванням: задача йде в ту саму чергу, що
    й решта, а готовий вердикт прилетить у перемальованому алерті. Людині
    показуємо рівно те, що сталось, — «поставлено в чергу», а не «готово».
    """
    _, exchange, merchant_id = call.data.split(":", 2)
    uid = call.from_user.id

    creds = await _db.credentials_for(uid)
    if not creds.is_personal:
        return await call.answer(
            "Спочатку підключи свій ключ: 🛡 Ріск-енджин → 🔑 Свої ключі до AI",
            show_alert=True,
        )
    # `not _llm_pool`, а не `is None`: це GlobalProxy, він ніколи не None.
    # `__bool__` віддає False, поки ціль не проставлена — а до старту
    # сканера вона саме така, бо пул створюється там.
    if not _llm_pool:
        return await call.answer("Черга аналізу ще не піднялась", show_alert=True)

    order = await _find_order(exchange, merchant_id)
    if order is None:
        return await call.answer(
            "Не знайшов цей ордер у кеші — він міг уже зникнути зі стакану.",
            show_alert=True,
        )

    from core.analysis.regex_analyzer import analyze as regex_analyze

    scheduled = _llm_pool.schedule(
        exchange=exchange,
        merchant_id=merchant_id,
        merchant_name=order.merchant_name,
        trade_terms=order.trade_terms or "",
        regex_result=regex_analyze(
            order.trade_terms or "", order.finish_rate_pct,
            order.month_order_count, order.is_verified,
        ),
        finish_rate=order.finish_rate_pct,
        month_order_count=order.month_order_count,
        is_verified=order.is_verified,
        min_limit=float(order.min_limit),
        max_limit=float(order.max_limit),
        review_summary=await _db.get_reviews_summary(exchange, merchant_id),
        coverage=getattr(order, "risk_coverage", None),
        credentials=creds,
    )
    if scheduled:
        await call.answer("🔑 Поставлено в чергу — оновлю алерт, щойно буде відповідь")
    else:
        await call.answer("Уже перевіряю цього мерчанта твоїм ключем", show_alert=True)


async def _find_order(exchange: str, merchant_id: str):
    """
    Ордер із кешів алертів — щоб не ходити на біржу заради умов.

    Умови тут не косметика: без них моделі нема що читати, а вигадувати
    вміст умов вона не повинна.
    """
    from bot.handlers import core as bot_commands

    for cache in (
        getattr(bot_commands, "_spread_cache", {}),
        getattr(bot_commands, "_taker_order_cache", {}),
    ):
        for value in list(cache.values()):
            candidate = value[0] if isinstance(value, tuple) else value
            for attr in ("buy_order", "sell_order"):
                order = getattr(candidate, attr, None)
                if order is not None and order.exchange == exchange \
                        and order.merchant_id == merchant_id:
                    return order
            if getattr(candidate, "exchange", "") == exchange \
                    and getattr(candidate, "merchant_id", "") == merchant_id:
                return candidate
    return None
