import logging
from datetime import datetime
from html import escape
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from exchanges.base import Order
from core.engine.price_advisor import PriceAdvisor
from bot.formatters import (
    EXCHANGE_ICONS,
    REC_LABELS,
    rec_badge,
    _profile_link,
    _verified_badge,
    _risk_badge,
)

logger = logging.getLogger(__name__)


async def send_maker_buy_suggestion(notifier, chat_id: int, advice: dict) -> None:
    """
    Надсилає рекомендацію оптимальної ціни купівлі (MAKER_BUY).
    advice: dict від PriceAdvisor.suggest_buy_price() + можливий buy_book_top
    """
    buy_book_top = advice.get("buy_book_top", 0)
    book_block = ""
    if buy_book_top > 0:
        diff = advice["max_buy_price"] - buy_book_top
        if diff > 0:
            book_block = (
                f"\n📊 <b>Стакан:</b> топ buy = <code>{buy_book_top:.2f}</code> ₴\n"
                f"✅ Можна поставити вище на <code>+{diff:.4f}</code> ₴ і залишитись в плюсі"
            )
        else:
            book_block = (
                f"\n📊 <b>Стакан:</b> топ buy = <code>{buy_book_top:.2f}</code> ₴\n"
                f"⚠️ Конкуренція висока — топ buy вже вище рекомендації на <code>{abs(diff):.4f}</code> ₴"
            )

    text = (
        "📥 <b>MAKER BUY: Аналіз ринку</b>\n\n"
        f"{PriceAdvisor.format_buy_suggestion(advice)}"
        f"{book_block}\n\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}\n"
        "<i>💡 Натисни «Створити оголошення» щоб виставити ad з рекомендованою ціною.</i>"
    )
    kb = [[InlineKeyboardButton(
        text="📢 Створити оголошення",
        callback_data="ad:create",
    )]]
    try:
        await notifier._send_with_retry(
            text,
            keyboard=InlineKeyboardMarkup(inline_keyboard=kb),
            disable_notification=False,
            chat_id=chat_id,
        )
    except Exception as e:
        logger.error("send_maker_buy_suggestion [%d]: %s", chat_id, e)


async def send_maker_sell_update(notifier, chat_id: int, advice: dict) -> None:
    """
    Надсилає оновлення рекомендованої ціни продажу (MAKER_SELL).
    advice: dict від PriceAdvisor.suggest_sell_price() + можливі sell_book_top, book_vs_min
    """
    book_top = advice.get("sell_book_top", 0)
    book_vs_min = advice.get("book_vs_min", 0)

    book_block = ""
    if book_top > 0:
        if book_vs_min > 0:
            book_block = (
                f"\n📊 <b>Стакан:</b> топ sell = <code>{book_top:.2f}</code> ₴\n"
                f"✅ Різниця з мін. ціною: <code>+{book_vs_min:.4f}</code> ₴ (вигідно!)"
            )
        else:
            book_block = (
                f"\n📊 <b>Стакан:</b> топ sell = <code>{book_top:.2f}</code> ₴\n"
                f"⚠️ Різниця з мін. ціною: <code>{book_vs_min:.4f}</code> ₴ (невигідно)"
            )

    text = (
        "📤 <b>MAKER SELL: Оновлення ціни</b>\n\n"
        f"{PriceAdvisor.format_sell_suggestion(advice)}"
        f"{book_block}\n\n"
        f"⏱ {datetime.now().strftime('%H:%M:%S')}\n"
        "<i>💡 Порада оновлена на основі поточного стакану.</i>"
    )
    kb = [[InlineKeyboardButton(
        text="📢 Створити / оновити оголошення",
        callback_data="ad:create",
    )]]
    try:
        await notifier._send_with_retry(
            text,
            keyboard=InlineKeyboardMarkup(inline_keyboard=kb),
            disable_notification=True,
            chat_id=chat_id,
        )
    except Exception as e:
        logger.error("send_maker_sell_update [%d]: %s", chat_id, e)


async def send_maker_order_alert(
    notifier,
    chat_id: int,
    order_info: dict,
    counterparty: Order,
    exchange: str,
) -> None:
    """
    Відправляє TG-нотифікацію про вхідний ордер на мейкер-оголошення.

    order_info: enriched dict з MakerAdMonitor
    counterparty: synthetic Order з даними контрагента (+ risk_flag, composite_score)
    """
    try:
        icon = EXCHANGE_ICONS.get(exchange, "◽️")
        name = _profile_link(exchange, counterparty.merchant_id, counterparty.merchant_name)
        verified = _verified_badge(counterparty)
        risk = _risk_badge(counterparty)

        # LLM вердикт
        rec = order_info.get("rec", "PENDING")
        reason = order_info.get("reason", "")
        rec_text = REC_LABELS.get((rec or "PENDING").upper(), f"🔍 {rec}")
        badge = rec_badge(rec)

        order_id = order_info.get("order_id", "")
        price = order_info.get("price", 0)
        amount_usdt = order_info.get("amount_usdt", 0)
        total_fiat = order_info.get("total_fiat", 0)
        item_id = order_info.get("item_id", "")

        text = (
            f"🔔 <b>НОВЕ ЗАМОВЛЕННЯ!</b>\n\n"
            f"{icon} <b>{escape(exchange)}</b> | "
            f"Оголошення: <code>{escape(str(item_id)[:20])}</code>\n\n"
            f"👤 <b>Контрагент:</b> {name}{verified}\n"
            f"📊 {counterparty.finish_rate_pct:.1f}% | "
            f"{counterparty.month_order_count} угод\n"
        )

        if risk:
            text += f"\n{risk}"

        text += (
            f"\n💰 <b>Сума:</b> <code>{total_fiat:.0f} ₴</code>"
            f" ({amount_usdt:.2f} USDT по {price:.2f})\n"
        )

        text += f"\n🧠 <b>AI:</b> {badge} {rec_text}\n"
        if reason and rec.upper() not in ("PENDING", "RECHECKING"):
            safe_reason = escape(str(reason).strip()[:300])
            text += f"<blockquote expandable>💬 {safe_reason}</blockquote>\n"

        # Кнопки
        kb_rows = []

        # Кешуємо order_id для кнопок
        short_oid = order_id[:20] if order_id else "?"
        kb_rows.append([
            InlineKeyboardButton(
                text="✅ Прийняти",
                callback_data=f"mkord:accept:{short_oid}",
            ),
            InlineKeyboardButton(
                text="❌ Відхилити",
                callback_data=f"mkord:reject:{short_oid}",
            ),
        ])

        # Лінк на біржу
        order_url = ""
        if exchange == "Bybit" and order_id:
            order_url = f"https://www.bybit.com/uk-UA/p2p/order/{order_id}"
        if order_url:
            kb_rows.append([InlineKeyboardButton(text="🔗 Відкрити на біржі", url=order_url)])

        # Бан контрагента
        mid = counterparty.merchant_id
        if mid:
            kb_rows.append([
                InlineKeyboardButton(
                    text="🚫 Бан контрагента",
                    callback_data=f"fb:{exchange}:{mid}:triangle",
                ),
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=kb_rows)

        for chunk in notifier._split_message(text):
            await notifier._send_with_retry(
                chunk,
                keyboard=keyboard if chunk == text else None,
                chat_id=chat_id,
            )

    except Exception as e:
        logger.error("send_maker_order_alert [%d]: %s", chat_id, e, exc_info=True)
