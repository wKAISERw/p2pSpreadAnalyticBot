# bot/handlers/risk.py
"""
Розділ «Ріск-енджин» — налаштування під себе.

Те, з чого починався весь реворк: «дуже потужний конфігуратор власних
проблем, по категоріях, зі своїми правилами». Тут воно й живе.

Два принципи, які пронизують усі екрани.

**Людина бачить наслідок, а не внутрішню назву.** Не «GEN_H02_TRIANGLE,
weight 100, layer hard», а «Треті особи та дропи — на купівлю попереджати,
на продаж ховати». Ключі лишаються в callback_data.

**Напрямок угоди видно завжди.** Купівля й продаж коштують різного, і саме
їхнє поєднання людина налаштовує. Показувати одну дію означало б ховати
половину сенсу.
"""
from __future__ import annotations

import logging
import re
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.handlers.core import _db
from bot.keyboards.risk import (
    ACTION_LABELS, CATEGORY_GROUPS, risk_back_kb, risk_categories_kb,
    risk_custom_kb, risk_group_kb, risk_main_kb, risk_profile_kb, risk_signal_kb,
)
from core.risk.policy import (
    ACTIONS, PROFILES, SIDE_BUY, SIDE_SELL, SignalPolicy, default_policy,
)
from core.risk.registry import CATEGORY_TITLES, builtin_registry
from core.risk.signals import SCOPE_REVIEWS, SCOPE_TERMS

router = Router()
logger = logging.getLogger("RiskSettings")

_GROUP_BY_ID = {gid: (title, cats) for gid, title, cats in CATEGORY_GROUPS}


class RiskStates(StatesGroup):
    waiting_test_text = State()
    waiting_signal_title = State()
    waiting_signal_phrases = State()


# ── Допоміжне ────────────────────────────────────────────────────────────────

async def _signals_for(user_id: int) -> list:
    """Вбудовані плюс власні сигнали користувача."""
    builtin = list(builtin_registry().signals)
    try:
        custom = await _db.get_user_signals(user_id)
    except Exception as e:
        logger.warning("Власні сигнали %s недоступні: %s", user_id, e)
        custom = []
    return builtin + custom


def _group_of(category: str) -> str | None:
    for gid, _, cats in CATEGORY_GROUPS:
        if category in cats:
            return gid
    return None


async def _render_main(call: CallbackQuery) -> None:
    uid = call.from_user.id
    profile = await _db.get_risk_profile(uid)
    policies = await _db.get_policies(uid)
    custom = await _db.get_user_signals(uid)

    text = (
        "🛡 <b>Ріск-енджин</b>\n\n"
        "Тут ви вирішуєте, на що бот реагує і як саме — окремо для купівлі "
        "й для продажу.\n\n"
        "<i>Чому окремо:</i> купуючи, ви обираєте, кому платити. Продаючи — "
        "приймаєте переказ на свою картку невідомо від кого. Той самий "
        "сигнал коштує різного.\n\n"
        f"<b>Профіль:</b> {PROFILES[profile]['title']}\n"
        f"<i>{PROFILES[profile]['hint']}</i>"
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text, reply_markup=risk_main_kb(profile, len(custom), len(policies))
        )


# ── Головне меню ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "risk:main")
@router.callback_query(F.data == "set:risk_engine")
async def cb_risk_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_main(call)
    await call.answer()


@router.callback_query(F.data == "risk:noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


# ── Профіль ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "risk:prof")
async def cb_profile_menu(call: CallbackQuery) -> None:
    current = await _db.get_risk_profile(call.from_user.id)
    lines = ["🎚 <b>Профіль суворості</b>\n"]
    for key, meta in PROFILES.items():
        mark = "✅" if key == current else "▫️"
        lines.append(f"{mark} <b>{meta['title']}</b>\n<i>{meta['hint']}</i>\n")
    lines.append(
        "<i>Профіль зсуває суворість усіх сигналів на крок. Те, що ви "
        "налаштували вручну, він не чіпає.</i>"
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text("\n".join(lines), reply_markup=risk_profile_kb(current))
    await call.answer()


@router.callback_query(F.data.startswith("risk:prof:"))
async def cb_profile_set(call: CallbackQuery) -> None:
    profile = call.data.split(":")[2]
    if not await _db.set_risk_profile(call.from_user.id, profile):
        return await call.answer("Невідомий профіль", show_alert=True)
    await call.answer(f"Профіль: {PROFILES[profile]['title']}")
    await cb_profile_menu(call)


# ── Категорії ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "risk:cats")
async def cb_categories(call: CallbackQuery) -> None:
    policies = await _db.get_policies(call.from_user.id)
    signals = await _signals_for(call.from_user.id)

    counts: dict[str, int] = {}
    for signal in signals:
        if signal.key in policies:
            gid = _group_of(signal.category)
            if gid:
                counts[gid] = counts.get(gid, 0) + 1

    text = (
        "📂 <b>Сигнали за категоріями</b>\n\n"
        "Оберіть групу, щоб побачити, як бот реагує на кожен сигнал "
        "окремо для купівлі й для продажу."
    )
    with suppress(TelegramBadRequest):
        await call.message.edit_text(text, reply_markup=risk_categories_kb(counts))
    await call.answer()


@router.callback_query(F.data.startswith("risk:grp:"))
async def cb_group(call: CallbackQuery) -> None:
    gid = call.data.split(":")[2]
    group = _GROUP_BY_ID.get(gid)
    if not group:
        return await call.answer("Групи не знайдено", show_alert=True)
    title, categories = group

    resolver = await _db.resolver_for(call.from_user.id)
    signals = await _signals_for(call.from_user.id)

    seen: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    for signal in signals:
        if signal.category not in categories or signal.category in seen:
            continue
        # Один рядок на категорію, а не на кожен патерн: у FINCRIME їх
        # вісім, і показувати вісім однакових пунктів означало б завалити
        # людину службовими подробицями.
        seen.add(signal.category)
        policy = resolver.for_signal(signal)
        rows.append((
            signal.key,
            CATEGORY_TITLES.get(signal.category, signal.category),
            policy.on_buy, policy.on_sell,
        ))

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            f"{title}\n\n<i>Натисніть сигнал, щоб змінити реакцію.</i>",
            reply_markup=risk_group_kb(gid, rows),
        )
    await call.answer()


# ── Один сигнал ──────────────────────────────────────────────────────────────

async def _render_signal(call: CallbackQuery, key: str) -> None:
    uid = call.from_user.id
    signals = await _signals_for(uid)
    signal = next((s for s in signals if s.key == key), None)
    if signal is None:
        return await call.answer("Сигнал не знайдено", show_alert=True)

    resolver = await _db.resolver_for(uid)
    policy = resolver.for_signal(signal)
    policies = await _db.get_policies(uid)
    tuned = key in policies
    is_custom = signal.owner != "builtin"

    scope = {SCOPE_TERMS: "умови мерчанта", SCOPE_REVIEWS: "тексти відгуків"}.get(
        signal.scope, "умови й відгуки"
    )
    lines = [
        f"<b>{CATEGORY_TITLES.get(signal.category, signal.category)}</b>",
        "",
    ]
    if signal.why:
        lines += [f"<i>{signal.why[:400]}</i>", ""]
    lines.append(f"Дивиться на: {scope}")
    if signal.example_risky:
        lines.append(f"Приклад ризику: «{signal.example_risky[:120]}»")
    if signal.negations:
        lines.append(
            "Не спрацює, якщо мерчант пише: "
            + ", ".join(f"«{n}»" for n in signal.negations[:4])
        )
    lines += [
        "",
        f"🛒 Купівля: <b>{ACTION_LABELS[policy.on_buy]}</b>",
        f"💸 Продаж: <b>{ACTION_LABELS[policy.on_sell]}</b>",
    ]
    if tuned:
        lines.append("\n<i>Налаштовано вручну — профіль це не змінює.</i>")

    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=risk_signal_kb(
                key, policy.on_buy, policy.on_sell, policy.enabled, is_custom, tuned
            ),
        )


@router.callback_query(F.data.startswith("risk:sig:"))
async def cb_signal(call: CallbackQuery) -> None:
    await _render_signal(call, call.data.split(":", 2)[2])
    await call.answer()


@router.callback_query(F.data.startswith("risk:act:"))
async def cb_set_action(call: CallbackQuery) -> None:
    _, _, key, side, action = call.data.split(":", 4)
    if action not in ACTIONS or side not in (SIDE_BUY, SIDE_SELL):
        return await call.answer("Невідома дія", show_alert=True)

    uid = call.from_user.id
    resolver = await _db.resolver_for(uid)
    signals = await _signals_for(uid)
    signal = next((s for s in signals if s.key == key), None)
    if signal is None:
        return await call.answer("Сигнал не знайдено", show_alert=True)

    # Пишемо ПОВНУ політику, а не одне поле: у базі немає «часткового»
    # налаштування, і другий бік мусить прийти з того, що діє зараз.
    updated = resolver.for_signal(signal).with_action(side, action)
    await _db.set_policy(uid, SignalPolicy(
        key, enabled=updated.enabled, on_buy=updated.on_buy, on_sell=updated.on_sell,
    ))
    await call.answer(ACTION_LABELS[action])
    await _render_signal(call, key)


@router.callback_query(F.data.startswith("risk:tog:"))
async def cb_toggle(call: CallbackQuery) -> None:
    key = call.data.split(":", 2)[2]
    uid = call.from_user.id
    resolver = await _db.resolver_for(uid)
    signals = await _signals_for(uid)
    signal = next((s for s in signals if s.key == key), None)
    if signal is None:
        return await call.answer("Сигнал не знайдено", show_alert=True)

    current = resolver.for_signal(signal)
    await _db.set_policy(uid, SignalPolicy(
        key, enabled=not current.enabled,
        on_buy=current.on_buy, on_sell=current.on_sell,
    ))
    await call.answer("Вимкнено" if current.enabled else "Увімкнено")
    await _render_signal(call, key)


@router.callback_query(F.data.startswith("risk:rst:"))
async def cb_reset_signal(call: CallbackQuery) -> None:
    key = call.data.split(":", 2)[2]
    await _db.reset_policy(call.from_user.id, key)
    await call.answer("Повернено дефолт")
    await _render_signal(call, key)


@router.callback_query(F.data == "risk:reset_all")
async def cb_reset_all(call: CallbackQuery) -> None:
    removed = await _db.reset_all_policies(call.from_user.id)
    await _db.set_risk_profile(call.from_user.id, "balanced")
    await call.answer(f"Скинуто налаштувань: {removed}")
    await _render_main(call)


# ── Власні сигнали ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "risk:mine")
async def cb_my_signals(call: CallbackQuery) -> None:
    custom = await _db.get_user_signals(call.from_user.id)
    text = (
        "➕ <b>Мої сигнали</b>\n\n"
        "Правила, які ви додали самі. Пишете фрази звичайною мовою — "
        "шаблон бот складе сам.\n"
    )
    if not custom:
        text += "\n<i>Поки нічого немає.</i>"
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            text, reply_markup=risk_custom_kb([(s.key, s.title) for s in custom])
        )
    await call.answer()


@router.callback_query(F.data == "risk:add")
async def cb_add_signal(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RiskStates.waiting_signal_title)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "➕ <b>Новий сигнал</b>\n\n"
            "Крок 1 із 2. Як його назвати?\n\n"
            "<i>Наприклад: «Просить оплатити частинами» або "
            "«Накопичувальний збір А-банку».</i>",
            reply_markup=risk_back_kb("risk:mine"),
        )
    await call.answer()


@router.message(RiskStates.waiting_signal_title)
async def on_signal_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()[:60]
    if not title:
        return await message.answer("Порожня назва. Спробуйте ще раз:")
    await state.update_data(title=title)
    await state.set_state(RiskStates.waiting_signal_phrases)
    await message.answer(
        f"Крок 2 із 2. Фрази для «{title}».\n\n"
        "Пишіть по одній на рядок, звичайною мовою — так, як це формулюють "
        "мерчанти.\n\n"
        "<i>Наприклад:</i>\n"
        "<code>накопичувальний збір\nа-банк збір\nна збір</code>\n\n"
        "<i>Бот сам врахує відмінки в межах фрази й не сплутає «збір» "
        "усередині «збірка».</i>"
    )


@router.message(RiskStates.waiting_signal_phrases)
async def on_signal_phrases(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    title = data.get("title", "Без назви")
    phrases = [p.strip() for p in (message.text or "").splitlines() if p.strip()]

    # Ключ із назви: латиниця й цифри, решта — підкреслення. Кирилиця в
    # callback_data з'їдає ліміт у 64 байти втричі швидше.
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:20]
    key = f"u_{slug or 'signal'}_{abs(hash(title)) % 10000}"

    ok, msg = await _db.save_user_signal(
        message.from_user.id, key=key, title=title, phrases=phrases,
        category="CUSTOM", why=f"Власне правило: {title}",
    )
    await state.clear()
    if not ok:
        return await message.answer(f"❌ {msg}\n\nСпробуйте ще раз через меню.")

    await message.answer(
        f"✅ Сигнал «{title}» збережено ({len(phrases)} фраз).\n\n"
        "За замовчуванням він попереджає й на купівлю, і на продаж — "
        "змінити можна в «Мої сигнали».\n\n"
        "<i>Перевірити, як він спрацьовує, можна через «🧪 Перевірити текст».</i>"
    )


@router.callback_query(F.data.startswith("risk:del:"))
async def cb_delete_signal(call: CallbackQuery) -> None:
    key = call.data.split(":", 2)[2]
    if await _db.delete_user_signal(call.from_user.id, key):
        await call.answer("Видалено")
    else:
        await call.answer("Не знайдено", show_alert=True)
    await cb_my_signals(call)


# ── Тест-стенд ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "risk:test")
async def cb_test(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RiskStates.waiting_test_text)
    with suppress(TelegramBadRequest):
        await call.message.edit_text(
            "🧪 <b>Перевірити текст</b>\n\n"
            "Надішліть умови мерчанта (або текст відгуку), і я покажу, що "
            "саме спрацює й чому — з вашими налаштуваннями.\n\n"
            "<i>Це єдиний спосіб перевірити своє правило до того, як воно "
            "почне мовчки різати ордери.</i>",
            reply_markup=risk_back_kb(),
        )
    await call.answer()


@router.message(RiskStates.waiting_test_text)
async def on_test_text(message: Message, state: FSMContext) -> None:
    from core.risk.matcher import match_text
    from core.risk.signals import SignalRegistry

    await state.clear()
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Порожній текст.")

    uid = message.from_user.id
    registry = SignalRegistry(await _signals_for(uid))
    resolver = await _db.resolver_for(uid)

    out: list[str] = [f"🧪 <b>Розбір</b>\n<code>{text[:200]}</code>\n"]
    for scope, label in ((SCOPE_TERMS, "Як умови мерчанта"), (SCOPE_REVIEWS, "Як текст відгуку")):
        found = match_text(text, scope, registry=registry)
        out.append(f"\n<b>{label}</b>")
        if not found.matches and not found.negated:
            out.append("  нічого не спрацювало")
            continue

        for m in found.matches:
            policy = resolver.for_signal(m.signal)
            out.append(
                f"  • {CATEGORY_TITLES.get(m.category, m.category)}\n"
                f"    цитата: «{m.excerpt[:70]}»\n"
                f"    купівля {ACTION_LABELS[policy.on_buy]} · "
                f"продаж {ACTION_LABELS[policy.on_sell]}"
            )
        for key in found.negated:
            signal = registry.by_key(key)
            name = CATEGORY_TITLES.get(signal.category, key) if signal else key
            # Заперечення показуємо явно: інакше «нічого не спрацювало»
            # виглядало б як «правило не працює», хоча воно спрацювало й
            # свідомо промовчало.
            out.append(f"  ⊘ {name} — не рахуємо, мерчант це заперечує")

    await message.answer("\n".join(out)[:4000], reply_markup=risk_back_kb())
