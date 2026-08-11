# bot/keyboards/risk.py
"""
Клавіатури розділу «Ріск-енджин».

Головний принцип екранів: людина бачить **наслідок**, а не назву правила.
Не «GEN_H02_TRIANGLE / weight 100 / layer hard», а «Треті особи та дропи —
на купівлю попереджати, на продаж ховати». Внутрішні ключі лишаються в
callback_data й нікуди не вилазять.

Категорія й напрямок стоять поруч на одному екрані навмисно: саме їхнє
поєднання людина й налаштовує («трикутник на продаж — інша річ, ніж на
купівлю»), і розводити їх по двох рівнях меню означало б змусити тримати
пів налаштування в голові.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.risk.policy import (
    BLOCK, IGNORE, NOTE, PROFILES, SIDE_BUY, SIDE_SELL, WARN,
)

# Дія → як вона виглядає людині. «block» тут це не «заблокувати мерчанта»,
# а «не показувати мені цей ордер»: рішення про показ, а не вирок.
ACTION_LABELS: dict[str, str] = {
    BLOCK: "🚫 ховати",
    WARN: "⚠️ попереджати",
    NOTE: "📎 згадати",
    IGNORE: "🙈 ігнорувати",
}

ACTION_ORDER = (BLOCK, WARN, NOTE, IGNORE)

# Категорії згруповані так, як про них думає людина, а не як вони лежать
# у реєстрі.
CATEGORY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pay", "💳 Куди йде платіж", ("PAYMENT_TARGET", "SUSPICIOUS_BIZ")),
    ("third", "👥 Треті особи", ("TRIANGLE", "THIRD_PARTY_HINT", "MIDDLEMAN")),
    ("offsite", "💬 Поза платформою", ("EXTERNAL_LINK", "CHAT_FIRST")),
    ("pressure", "⚖️ Тиск і апеляції", ("APPEAL_PRESSURE", "CHARGEBACK", "RECEIPT_REQUIRED")),
    ("forbidden", "🎰 Заборонена сфера", ("CASINO", "FINCRIME", "ANONYMOUS", "NO_COMMENTS")),
    ("reviews", "🗣 Відгуки", ("SCAM_REPORT",)),
)


def risk_main_kb(profile: str, custom_count: int, tuned_count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    title = PROFILES.get(profile, {}).get("title", profile)
    b.row(InlineKeyboardButton(text=f"🎚 Профіль: {title}", callback_data="risk:prof"))
    b.row(InlineKeyboardButton(
        text=f"📂 Сигнали за категоріями{f' ({tuned_count} змінено)' if tuned_count else ''}",
        callback_data="risk:cats",
    ))
    b.row(InlineKeyboardButton(
        text=f"➕ Мої сигнали{f' ({custom_count})' if custom_count else ''}",
        callback_data="risk:mine",
    ))
    # Тест-стенд не прикраса: це єдиний спосіб для людини перевірити свою
    # фразу до того, як вона почне мовчки різати ордери.
    b.row(InlineKeyboardButton(text="🧪 Перевірити текст", callback_data="risk:test"))
    b.row(InlineKeyboardButton(text="🔑 Свої ключі до AI", callback_data="byok:main"))
    if tuned_count or profile != "balanced":
        b.row(InlineKeyboardButton(text="♻️ Скинути все до дефолтів", callback_data="risk:reset_all"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu:settings"))
    return b.as_markup()


def risk_profile_kb(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, meta in PROFILES.items():
        mark = "✅ " if key == current else ""
        b.row(InlineKeyboardButton(text=f"{mark}{meta['title']}", callback_data=f"risk:prof:{key}"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="risk:main"))
    return b.as_markup()


def risk_categories_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for group_id, title, _ in CATEGORY_GROUPS:
        tuned = counts.get(group_id, 0)
        suffix = f" · {tuned} змінено" if tuned else ""
        b.row(InlineKeyboardButton(text=f"{title}{suffix}", callback_data=f"risk:grp:{group_id}"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="risk:main"))
    return b.as_markup()


def risk_group_kb(group_id: str, rows: list[tuple[str, str, str, str]]) -> InlineKeyboardMarkup:
    """
    rows: (ключ_сигналу, назва, дія_на_купівлю, дія_на_продаж).

    Обидва напрямки видно одразу в підписі — інакше, щоб побачити повну
    картину, довелось би заходити в кожен сигнал окремо.
    """
    b = InlineKeyboardBuilder()
    for key, title, on_buy, on_sell in rows:
        buy = ACTION_LABELS.get(on_buy, on_buy).split()[0]
        sell = ACTION_LABELS.get(on_sell, on_sell).split()[0]
        b.row(InlineKeyboardButton(
            text=f"{title} · купівля {buy} · продаж {sell}",
            callback_data=f"risk:sig:{key}",
        ))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="risk:cats"))
    return b.as_markup()


def risk_signal_kb(key: str, on_buy: str, on_sell: str,
                   enabled: bool, is_custom: bool, tuned: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()

    b.row(InlineKeyboardButton(
        text=("✅ Сигнал увімкнено" if enabled else "⛔️ Сигнал вимкнено"),
        callback_data=f"risk:tog:{key}",
    ))

    for side, current, label in ((SIDE_BUY, on_buy, "🛒 Купівля"), (SIDE_SELL, on_sell, "💸 Продаж")):
        b.row(InlineKeyboardButton(text=f"— {label} —", callback_data="risk:noop"))
        buttons = [
            InlineKeyboardButton(
                text=("• " if action == current else "") + ACTION_LABELS[action],
                callback_data=f"risk:act:{key}:{side}:{action}",
            )
            for action in ACTION_ORDER
        ]
        b.row(*buttons[:2])
        b.row(*buttons[2:])

    if tuned:
        b.row(InlineKeyboardButton(text="♻️ Повернути дефолт", callback_data=f"risk:rst:{key}"))
    if is_custom:
        b.row(InlineKeyboardButton(text="🗑 Видалити сигнал", callback_data=f"risk:del:{key}"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="risk:cats"))
    return b.as_markup()


def risk_custom_kb(signals: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, title in signals:
        b.row(InlineKeyboardButton(text=f"✏️ {title}", callback_data=f"risk:sig:{key}"))
    b.row(InlineKeyboardButton(text="➕ Додати свій сигнал", callback_data="risk:add"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="risk:main"))
    return b.as_markup()


def risk_back_kb(to: str = "risk:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=to)]
    ])
