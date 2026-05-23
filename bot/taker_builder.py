import asyncio
import logging
import time
from datetime import datetime
from html import escape
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from exchanges.base import Order
from core.analytics.merchant_profile import build_profile_url
from bot.handlers import core as bot_commands
from bot.formatters import (
    EXCHANGE_ICONS,
    _bank_code_to_db,
    rec_badge,
    _llm_verdict_block,
    _terms_block,
    _format_bank_list,
    _profile_link,
    _verified_badge,
    _risk_badge,
    _regex_warn_block,
)

logger = logging.getLogger(__name__)


async def send_taker_to_user(
    notifier, chat_id: int, orders: list[Order], mode: str,
) -> None:
    """
    Відправляє тейкер-алерти юзеру — КОЖЕН ордер як окреме повідомлення
    в тому ж дизайні що й spread-алерт, але з однією стороною.
    mode: TAKER_BUY або TAKER_SELL
    """
    if not orders:
        return
    ds = await notifier._get_display_settings(chat_id)
    for i, order in enumerate(orders[:10]):
        try:
            await send_taker_single(notifier, order, mode, chat_id=chat_id, display_settings=ds)
            if i < len(orders) - 1:
                await asyncio.sleep(0.8)
        except Exception as e:
            logger.error("send_taker_to_user [%d] order #%d: %s", chat_id, i, e)


async def send_taker_single(
    notifier, order: Order, mode: str,
    chat_id: int | None = None,
    display_settings: dict | None = None,
) -> None:
    """
    Відправляє ОДИН тейкер-ордер як повноцінне повідомлення.
    Повністю інтегровано з кастомізацією карткового модуля (Inline/Reply, Smart-Spoilers, Compact).
    """
    ds = display_settings or {
        "show_ai_terms_summary": True, "show_full_terms": True,
        "show_ai_logic": True, "show_bank_details": True, "show_llm_summary": True,
    }

    is_buy = mode == "TAKER_BUY"
    side_title = "🛒 <b>ТЕЙКЕР: КУПІВЛЯ</b>" if is_buy else "💸 <b>ТЕЙКЕР: ПРОДАЖ</b>"
    side_label = "КУПУЄМО" if is_buy else "ПРОДАЄМО"
    side_icon = "🛒" if is_buy else "💸"

    # ── Refresh LLM verdict from DB ──
    llm_rec = "PENDING"
    llm_reason = ""
    terms_summary = ""
    rev_analysis = ""
    if notifier._db:
        try:
            rec, _, reason, t_sum, rev_analyz = await notifier._db.get_trade_recommendation_full(
                order.exchange, order.merchant_id,
            )
            llm_rec = rec
            llm_reason = reason
            terms_summary = t_sum
            rev_analysis = rev_analyz
        except Exception:
            pass

    icon = EXCHANGE_ICONS.get(order.exchange, "◽️")
    merchant_link = _profile_link(order.exchange, order.merchant_id, order.merchant_name)
    name_str = f"{rec_badge(llm_rec)} {merchant_link}{_verified_badge(order)}"

    risk_block = _risk_badge(order)
    warn_block = _regex_warn_block(order)

    banks_all = _format_bank_list(order.bank_codes)

    now = datetime.now()
    silent = False  # тейкер — завжди зі звуком

    text = (
        f"{side_title}\n\n"
        f"⏱ {now.strftime('%H:%M:%S')} | {icon} <b>{escape(order.exchange)}</b>\n\n"
    )

    # ── Деталі банків ──
    if ds.get("show_bank_details", True):
        text += (
            f"<blockquote expandable>"
            f"🏦 Банки: {banks_all}"
            f"</blockquote>\n"
        )

    # ── Основний блок ордера ──
    terms_raw = getattr(order, "trade_terms", "") or ""
    text += (
        f"{side_icon} <b>{side_label}</b>\n"
        f"Курс: <code>{escape(str(order.price))}</code>\n"
        f"Мерчант: {name_str} "
        f"({order.finish_rate_pct:.1f}% | {order.month_order_count} угод)\n"
        f"Ліміти: <code>{escape(str(order.min_limit))}–{escape(str(order.max_limit))} ₴</code>"
        f"  💎 <code>{float(order.available_amount):.0f} USDT</code> в ордері\n"
        f"{risk_block if risk_block else ''}"
        f"{warn_block if warn_block else ''}"
    )

    # ── Блок умов ──
    terms_blk = _terms_block(
        terms_raw,
        terms_summary=terms_summary,
        show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
        show_full_terms=ds.get("show_full_terms", True),
    )
    if terms_blk:
        text += terms_blk

    # ── LLM Verdict ──
    if ds.get("show_llm_summary", True):
        llm_block = _llm_verdict_block(
            "Buy" if is_buy else "Sell", llm_rec, llm_reason,
            terms_summary=terms_summary,
            show_ai_logic=ds.get("show_ai_logic", True),
            show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
            reviews_analysis=rev_analysis
        )
    else:
        llm_block = _llm_verdict_block(
            "Buy" if is_buy else "Sell", llm_rec, "",
            show_ai_logic=False, show_ai_terms_summary=False,
        )
    text += f"\n{llm_block}"

    # ── Кнопки ──
    kb: list[list[InlineKeyboardButton]] = []

    ad_id = getattr(order, "ad_id", getattr(order, "order_id", order.id))
    if ad_id and llm_rec != "REJECT":
        # Кнопка ⚡ Взяти
        if hasattr(notifier, "_taker_cache") and notifier._taker_cache is not None:
            cache_key = f"tk_{ad_id[:12]}_{int(time.time()) % 10000}"
            notifier._taker_cache.set(cache_key, {
                "ad_id": str(ad_id),
                "exchange": order.exchange,
                "price": float(order.price),
                "merchant_id": order.merchant_id,
                "min_limit": float(order.min_limit),
                "max_limit": float(order.max_limit),
                "bank": (order.bank_codes[0] if order.bank_codes else ""),
                "direction": "b" if is_buy else "s",
                "action": "BUY" if is_buy else "SELL",
                "ts": time.time(),
            })
            action_label = "Купити" if is_buy else "Продати"
            kb.append([InlineKeyboardButton(
                text=f"⚡ {action_label} ({order.exchange} {order.price})",
                callback_data=f"taker:take:{cache_key}",
            )])

        # Single-Leg кнопка
        bank_code = (order.bank_codes[0] if order.bank_codes else "")
        direction = "b" if is_buy else "s"
        sl_key = f"{str(ad_id)[:10]}|{order.exchange[:3]}|{bank_code[:4]}"
        bot_commands._single_leg_cache[f"{direction}:{sl_key}"] = {
            "ad_id": str(ad_id), "exchange": order.exchange,
            "price": float(order.price),
            "merchant_id": order.merchant_id,
            "min_limit": float(order.min_limit),
            "max_limit": float(order.max_limit),
            "bank": bank_code, "ts": time.time(),
        }
        sl_label = "🛒 Купити" if is_buy else "💸 Продати"
        kb.append([InlineKeyboardButton(
            text=f"{sl_label} ({order.exchange})",
            callback_data=f"sl:{direction}:{sl_key}",
        )])

    # URL-кнопка
    url = getattr(order, "link", "") or build_profile_url(
        order.exchange, order.merchant_id
    )
    if url:
        kb.append([InlineKeyboardButton(text="🔗 На біржі", url=url)])

    # Blacklist кнопки
    mid = order.merchant_id
    if mid:
        prefix = "🔴 Buy" if is_buy else "🔵 Sell"
        kb.append([
            InlineKeyboardButton(text=f"{prefix}: 3-ті",
                                 callback_data=f"fb:{order.exchange}:{mid}:triangle"),
            InlineKeyboardButton(text="Чек", callback_data=f"fb:{order.exchange}:{mid}:receipt"),
            InlineKeyboardButton(text="ТГ", callback_data=f"fb:{order.exchange}:{mid}:chat"),
        ])

    # ── КАРТКОВИЙ БЛОК З СИНХРОНІЗАЦІЄЮ НАЛАШТУВАНЬ ВИТЯГУ (Для TAKER) ──
    card_text_combined = ""
    card_keyboard_rows = []
    output_mode = "inline"

    if ds.get("show_card_recommendation", True) and getattr(notifier, "card_notifier", None):
        card_settings = await notifier._db.get_user_card_settings(chat_id or notifier._chat_id) or {}

        # 🔌 СУВОРИЙ ГАРД-КЛАУЗ: Перевіряємо чи дозволив користувач вивід карт в одиночних режимах
        if card_settings.get("enable_in_single_modes"):
            bank_code = order.bank_codes[0] if order.bank_codes else ""
            card_bank = _bank_code_to_db(bank_code)
            card_direction = "buy" if is_buy else "sell"
            card_order_id = str(ad_id) if ad_id else ""

            # 🚀 ФІКС ТУПЛА: Розпаковуємо 3 значення, ігноруючи словник карти через "_"
            card_text, card_rows, _ = await notifier.card_notifier.get_card_block(
                chat_id=chat_id or notifier._chat_id,
                target_amount=float(order.min_limit),
                direction=card_direction,
                bank=card_bank,
                order_id=card_order_id,
            )

            if card_text:
                card_text_combined = card_text
                card_keyboard_rows = card_rows
                output_mode = card_settings.get("card_output_mode", "inline")

                # Якщо режим inline — інжектуємо дані прямо в тіло та клавіатуру
                if output_mode == "inline":
                    text += f"\n{card_text_combined}"
                    kb.extend(card_keyboard_rows)

    # ── ФІНАЛЬНА СЕЛЕКЦІЯ І НАДШИЛАННЯ В ТЕЛЕГРАМ ──
    if card_text_combined and output_mode == "reply":
        # 🔀 РЕЖИМ REPLY: Відокремлюємо карти в окремий зв'язаний потік (thread)
        main_keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
        card_keyboard = InlineKeyboardMarkup(inline_keyboard=card_keyboard_rows)

        # 1. Запускаємо основне тіло Тейкер-алерта (чисте, без карткового контенту)
        main_chunks = notifier._split_message(text)
        main_msg = None
        for i, chunk in enumerate(main_chunks):
            main_msg = await notifier._send_with_retry(
                chunk,
                keyboard=main_keyboard if i == 0 else None,
                disable_notification=silent,
                chat_id=chat_id,
            )

        # 2. Стріляємо реплаєм суто по картках з прив'язкою до початкового повідомлення
        if main_msg and hasattr(main_msg, "message_id"):
            reply_text = f"💳 <b>Рекомендована картка під Тейкер-операцію:</b>\n\n{card_text_combined}"
            await notifier._send_with_retry(
                reply_text,
                keyboard=card_keyboard,
                disable_notification=silent,
                chat_id=chat_id,
                reply_to_message_id=main_msg.message_id
            )
    else:
        # 📥 ДЕФОЛТНИЙ INLINE АБО СТАН БЕЗ КАРТ
        keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
        chunks = notifier._split_message(text)
        for i, chunk in enumerate(chunks):
            await notifier._send_with_retry(
                chunk,
                keyboard=keyboard if i == 0 else None,
                disable_notification=silent,
                chat_id=chat_id,
            )
