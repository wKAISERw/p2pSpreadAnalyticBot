import asyncio
import logging
import time
from datetime import datetime
from html import escape
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from exchanges.base import Order
from core.analytics.merchant_profile import build_profile_url, build_app_profile_url
from bot.deeplinks import resolve_target, tg_button_url, tg_button_url_async
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


async def _transfer_fee(notifier, chat_id: int | None, order: Order, is_buy: bool):
    """
    Комісія банку за переказ фіату під цей ордер: (сума ₴, опис, курс із комісією).

    Повертає None, коли комісії немає або рахувати нема від чого.

    Навіщо взагалі: калькулятор комісій існував, але викликався рівно з
    одного місця — `cross_matcher`, тобто лише в спред-режимі. А банківський
    переказ фіату відбувається саме в тейкерських: там комісія завжди
    показувалась нулем. При спреді 0.5–1% комісія А-Банку 2% з'їдає весь
    профіт, і побачити це було нізвідки.

    Тільки для TAKER_BUY: у TAKER_SELL фіат відправляє мерчант, і комісію
    свого банку платить він, а не ми.
    """
    if not is_buy or not order.bank_codes:
        return None

    from config.banks import normalize_bank
    from core.utils.fees import bank_transfer_fee

    bank = normalize_bank(order.bank_codes[0])

    # У тейкері переказ завжди йде на той самий банк: движок добирає картку
    # рівно того банку, який приймає мерчант. Тож «міжбанківські» комісії
    # (ПриватБанк, Monobank) тут не виникають.
    fee_obj = bank_transfer_fee(bank, bank)
    if fee_obj is None:
        return None

    price = float(order.price)
    if price <= 0:
        return None

    # Сума, яку реально відправимо. Беремо обсяг користувача, а не мінімалку
    # ордера: комісія з порогом («до 20к — 0%») від суми залежить прямо.
    amount_uah = float(order.min_limit)
    try:
        if notifier._db and chat_id:
            user = await notifier._db.get_user_by_id(chat_id)
            desired = float((user or {}).get("taker_buy_amount", 0) or 0)
            if desired > 0:
                amount_uah = max(
                    float(order.min_limit),
                    min(desired * price, float(order.max_limit)),
                )
    except Exception as e:
        logger.debug("Не вдалось узяти обсяг купівлі для комісії: %s", e)

    if amount_uah <= 0:
        return None

    result = fee_obj.calculate(amount_uah, price)
    if result.amount <= 0:
        return None

    effective_price = price * (1 + result.amount / amount_uah)
    return result.amount, result.description, effective_price


async def send_taker_to_user(
    notifier, chat_id: int, orders: list[Order], mode: str,
    group: bool | None = None,
) -> None:
    """
    Відправляє тейкер-алерти юзеру.
    Якщо ордер один — відправляє його детально.
    Якщо ордерів кілька — відправляє їх об'єднаним компактним списком, щоб уникнути флуду.
    mode: TAKER_BUY або TAKER_SELL

    group:
        None  — вирішуємо за кількістю (стара поведінка, для авто-алертів);
        False — завжди окремими повідомленнями, навіть якщо ордерів багато;
        True  — зводимо в одне, якщо ордерів більше одного.

    Параметр з'явився через баг: тумблер «Групувати /active в 1 повідомлення»
    діяв лише в режимі SPREAD, а тейкерні режими його ігнорували і завжди
    зводили все в одне повідомлення.
    """
    if not orders:
        return
    ds = await notifier._get_display_settings(chat_id)

    if group is False and len(orders) > 1:
        for order in orders:
            try:
                await send_taker_single(notifier, order, mode, chat_id=chat_id,
                                        display_settings=ds)
            except Exception as e:
                logger.error("send_taker_to_user [%d] single-of-many error: %s", chat_id, e)
        return

    if len(orders) == 1:
        try:
            await send_taker_single(notifier, orders[0], mode, chat_id=chat_id, display_settings=ds)
        except Exception as e:
            logger.error("send_taker_to_user [%d] single order error: %s", chat_id, e)
    else:
        try:
            await send_taker_combined(notifier, chat_id, orders, mode, display_settings=ds)
        except Exception as e:
            logger.error("send_taker_to_user [%d] combined orders error: %s", chat_id, e)


async def send_taker_combined(
    notifier, chat_id: int, orders: list[Order], mode: str, display_settings: dict,
) -> None:
    """
    Відправляє список тейкер-ордерів одним компактним повідомленням.
    Це запобігає флуду та обмеженням Telegram (Too Many Requests).
    """
    is_buy = mode == "TAKER_BUY"
    side_title = "📡 <b>АКТИВНІ ОРДЕРИ: КУПІВЛЯ (Taker)</b>" if is_buy else "📡 <b>АКТИВНІ ОРДЕРИ: ПРОДАЖ (Taker)</b>"
    
    total = len(orders)
    top_count = min(total, 5)  # покажемо топ-5 для компактності
    
    now = datetime.now()
    text = (
        f"{side_title}\n"
        f"⏱ {now.strftime('%H:%M:%S')} | Всього знайдено: <b>{total}</b> шт.\n"
        f"Показано топ-{top_count} найвигідніших:\n\n"
    )
    
    kb: list[list[InlineKeyboardButton]] = []
    
    for idx, order in enumerate(orders[:top_count], start=1):
        icon = EXCHANGE_ICONS.get(order.exchange, "◽️")
        
        # ── Refresh LLM verdict from DB ──
        llm_rec = "PENDING"
        llm_reason = ""
        if notifier._db:
            try:
                rec, _, reason, _, _ = await notifier._db.get_trade_recommendation_full(
                    order.exchange, order.merchant_id,
                )
                llm_rec = rec
                llm_reason = reason
            except Exception:
                pass
                
        # Badge для вердикту
        rec_str = rec_badge(llm_rec)
        
        # Форматуємо банки
        banks = _format_bank_list(order.bank_codes)
        
        # Посилання на мерчанта
        profile_mode = display_settings.get("cryptobot_profile_mode", "chat")
        merchant_link = _profile_link(
            order.exchange, order.merchant_id, order.merchant_name,
            side="buy" if is_buy else "sell",
            profile_mode=profile_mode,
            offer_id=str(order.id),
        )
        verified = _verified_badge(order)
        subs_badge = " 🎁" if getattr(order, "is_new_user_subsidy", False) else ""
        
        text += (
            f"<b>{idx}. {icon} {order.exchange}</b> | <b>{escape(str(order.price))} ₴</b>\n"
            f"   👤 {rec_str} {merchant_link}{verified}{subs_badge} ({order.finish_rate_pct:.1f}% | {order.month_order_count} угод)\n"
            f"   🏦 Банки: <code>{banks}</code>\n"
            f"   💵 Ліміти: <code>{escape(str(order.min_limit))}–{escape(str(order.max_limit))} ₴</code> ({float(order.available_amount):.1f} USDT)\n"
        )
        # Комісія переказу. У зведеному списку вона важить навіть більше, ніж
        # в одиночному алерті: тут ордери стоять поруч і порівнюються за
        # ціною, а «найдешевший» за курсом може виявитись не найдешевшим
        # після комісії банку.
        fee_info = await _transfer_fee(notifier, chat_id, order, is_buy)
        if fee_info:
            fee_uah, _fee_desc, eff_price = fee_info
            fee_str = f"{fee_uah:,.0f}".replace(",", " ")
            text += (
                f"   💳 З комісією: <b>{eff_price:.4f} ₴</b> "
                f"<i>(+{fee_str} ₴ за переказ)</i>\n"
            )

        if llm_reason and display_settings.get("show_ai_logic", True):
            reason_short = llm_reason[:120] + "..." if len(llm_reason) > 120 else llm_reason
            text += f"   🧠 <i>{escape(reason_short)}</i>\n"
        text += "\n"
        
        # Кнопки для взяття цього ордеру
        ad_id = getattr(order, "ad_id", getattr(order, "order_id", order.id))
        if ad_id and llm_rec != "REJECT":
            if hasattr(notifier, "_taker_cache") and notifier._taker_cache is not None:
                cache_key = f"tk_{ad_id[:12]}_{int(time.time()) % 10000}_{idx}"
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
                row = [InlineKeyboardButton(
                    text=f"⚡ {idx}. {action_label} ({order.exchange} {order.price})",
                    callback_data=f"taker:take:{cache_key}",
                )]
                # 📱 Відкрити в застосунку — те саме, що в одиночному алерті.
                # Раніше цієї кнопки в зведеному списку не було взагалі, тож
                # зайти в апку до мерчанта зі списку було неможливо.
                _kind, _eid = resolve_target(order)
                if _eid:
                    try:
                        app_url = await tg_button_url_async(
                            order.exchange, _kind, _eid,
                            side="buy" if is_buy else "sell",
                            web_fallback=getattr(order, "link", "") or build_profile_url(
                                order.exchange, order.merchant_id),
                            db=getattr(notifier, "_db", None),
                        )
                        if app_url:
                            row.append(InlineKeyboardButton(text="📱 App", url=app_url))
                    except Exception as e:
                        logger.warning("taker combined: app-кнопка для %s впала: %s",
                                       order.exchange, e)
                kb.append(row)
                
    # ── Додаємо картковий блок для топ-1 ордера ──
    card_text_combined = ""
    if display_settings.get("show_card_recommendation", True) and getattr(notifier, "card_notifier", None):
        card_settings = await notifier._db.get_user_card_settings(chat_id) or {}
        if card_settings.get("enable_in_single_modes") and orders:
            best_order = orders[0]
            bank_code = best_order.bank_codes[0] if best_order.bank_codes else ""
            card_bank = _bank_code_to_db(bank_code)
            card_direction = "buy" if is_buy else "sell"
            best_ad_id = getattr(best_order, "ad_id", getattr(best_order, "order_id", best_order.id))
            card_text, _, _ = await notifier.card_notifier.get_card_block(
                chat_id=chat_id,
                target_amount=float(best_order.min_limit),
                direction=card_direction,
                bank=card_bank,
                order_id=str(best_ad_id) if best_ad_id else "",
            )
            if card_text:
                card_text_combined = card_text
                
    if card_text_combined:
        text += (
            f"💳 <b>Рекомендована картка під топ-1 ордер:</b>\n"
            f"{card_text_combined}"
        )
        
    await notifier._send_with_retry(
        text,
        keyboard=InlineKeyboardMarkup(inline_keyboard=kb) if kb else None,
        chat_id=chat_id,
    )


async def send_taker_single(
    notifier, order: Order, mode: str,
    chat_id: int | None = None,
    display_settings: dict | None = None,
    edit_message_ids: list[int] | None = None,
) -> list[int]:
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
    profile_mode = ds.get("cryptobot_profile_mode", "chat")
    merchant_link = _profile_link(
        order.exchange, order.merchant_id, order.merchant_name,
        side="buy" if is_buy else "sell",
        profile_mode=profile_mode,
        offer_id=str(order.id),
    )
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
        f"  💎 <code>{float(order.available_amount):.2f} USDT</code> в ордері\n"
        f"{risk_block if risk_block else ''}"
        f"{warn_block if warn_block else ''}"
    )

    # ── Міжбіржовий переказ USDT (тільки для продажу) ──
    #
    # Продаємо ми те, що вже маємо, і воно лежить на конкретній біржі.
    # Якщо ордер на іншій — USDT доведеться перевести, а це комісія мережі,
    # яка при спреді 0.5–1% помітна. Досі цього не рахував ніхто: калькулятор
    # мереж викликався лише зі спред-режиму.
    if not is_buy:
        try:
            from core.engine.usdt_inventory import (
                plan_transfer, render_plan, usdt_by_exchange,
            )

            uid = chat_id or notifier._chat_id
            sell_amount = float((await notifier._db.get_user_by_id(uid) or {})
                                .get("taker_sell_amount", 0) or 0)
            if sell_amount > 0:
                balances = await usdt_by_exchange(notifier._db, uid)
                plan_line = render_plan(
                    plan_transfer(balances, order.exchange, sell_amount),
                    price_uah=float(order.price),
                )
                if plan_line:
                    text += plan_line
        except Exception as e:
            logger.debug("Не вдалось порахувати міжбіржовий переказ: %s", e)

    # ── Комісія банківського переказу ──
    fee_info = await _transfer_fee(notifier, chat_id or notifier._chat_id, order, is_buy)
    if fee_info:
        fee_uah, fee_desc, eff_price = fee_info
        # Пробіл-роздільник ставимо лише в числі: .replace на всьому рядку
        # покалічив би опис комісії, якби в ньому колись з'явилась кома.
        fee_str = f"{fee_uah:,.2f}".replace(",", " ")
        text += (
            f"💳 Комісія переказу: <code>{fee_str} ₴</code> "
            f"<i>({escape(fee_desc)})</i>\n"
            f"📉 Курс із комісією: <code>{eff_price:.4f}</code> "
            f"<i>(замість {float(order.price):.4f})</i>\n"
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

    # URL-кнопки (Web + Redirect App)
    url = getattr(order, "link", "") or build_profile_url(
        order.exchange, order.merchant_id
    )
    url_row = []
    if url:
        url_row.append(InlineKeyboardButton(text="🔗 На біржі", url=url))
        
    # 📱 App-кнопка. Binance/Bybit відкриваються прямим https-диплінком,
    # OKX — через redirect.html (у нього немає зареєстрованих https-шляхів на P2P).
    _kind, _eid = resolve_target(order)
    if _eid:
        app_url = await tg_button_url_async(
            order.exchange, _kind, _eid,
            side="buy" if is_buy else "sell",
            web_fallback=url,
            db=getattr(notifier, "_db", None),
        )
        if app_url:
            url_row.append(InlineKeyboardButton(text="📱 Відкрити в App", url=app_url))
        
    if url_row:
        kb.append(url_row)

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

            # Маршрут рахуємо тим самим кодом, що й сканер: якщо алерт
            # намалює картки не з тих банків, які движок вважав придатними,
            # розбіжність спливе аж у момент угоди.
            from core.engine.card_routing import resolve_route

            route = await resolve_route(
                notifier._db, chat_id or notifier._chat_id,
                order.bank_codes, primary_bank=card_bank,
            )

            # 🚀 ФІКС ТУПЛА: Розпаковуємо 3 значення, ігноруючи словник карти через "_"
            card_text, card_rows, _ = await notifier.card_notifier.get_card_block(
                chat_id=chat_id or notifier._chat_id,
                target_amount=float(order.min_limit),
                direction=card_direction,
                bank=card_bank,
                order_id=card_order_id,
                route_banks=route.banks or None,
                declared_banks=route.declared,
            )

            if card_text:
                card_text_combined = card_text
                card_keyboard_rows = card_rows
                output_mode = card_settings.get("card_output_mode", "inline")

                # Якщо режим inline — інжектуємо дані прямо в тіло та клавіатуру
                if output_mode == "inline":
                    text += f"\n{card_text_combined}"
                    kb.extend(card_keyboard_rows)

    # ── ФІНАЛЬНА СЕЛЕКЦІЯ І НАДШИЛАННЯ/РЕДАГУВАННЯ В ТЕЛЕГРАМ ──
    sent_message_ids = []
    if card_text_combined and output_mode == "reply":
        # 🔀 РЕЖИМ REPLY: Відокремлюємо карти в окремий зв'язаний потік (thread)
        main_keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
        card_keyboard = InlineKeyboardMarkup(inline_keyboard=card_keyboard_rows)

        # 1. Запускаємо основне тіло Тейкер-алерта (чисте, без карткового контенту)
        main_chunks = notifier._split_message(text)
        main_msg = None
        for i, chunk in enumerate(main_chunks):
            edited_msg_id = edit_message_ids[i] if edit_message_ids and i < len(edit_message_ids) else None
            if edited_msg_id:
                try:
                    await notifier._edit_with_retry(
                        message_id=edited_msg_id,
                        text=chunk,
                        keyboard=main_keyboard if i == 0 else None,
                        chat_id=chat_id,
                    )
                    sent_message_ids.append(edited_msg_id)
                    if i == 0:
                        class DummyMsg:
                            def __init__(self, mid):
                                self.message_id = mid
                        main_msg = DummyMsg(edited_msg_id)
                except Exception as ex:
                    logger.error("Failed to edit main message chunk %d: %s", edited_msg_id, ex)
            else:
                main_msg = await notifier._send_with_retry(
                    chunk,
                    keyboard=main_keyboard if i == 0 else None,
                    disable_notification=silent,
                    chat_id=chat_id,
                )
                if main_msg and hasattr(main_msg, "message_id"):
                    sent_message_ids.append(main_msg.message_id)

        # 2. Стріляємо реплаєм суто по картках з прив'язкою до початкового повідомлення
        if main_msg and hasattr(main_msg, "message_id"):
            reply_text = f"💳 <b>Рекомендована картка під Тейкер-операцію:</b>\n\n{card_text_combined}"
            edited_reply_msg_id = edit_message_ids[len(main_chunks)] if edit_message_ids and len(main_chunks) < len(edit_message_ids) else None
            if edited_reply_msg_id:
                try:
                    await notifier._edit_with_retry(
                        message_id=edited_reply_msg_id,
                        text=reply_text,
                        keyboard=card_keyboard,
                        chat_id=chat_id,
                    )
                    sent_message_ids.append(edited_reply_msg_id)
                except Exception as ex:
                    logger.error("Failed to edit reply message %d: %s", edited_reply_msg_id, ex)
            else:
                reply_msg = await notifier._send_with_retry(
                    reply_text,
                    keyboard=card_keyboard,
                    disable_notification=silent,
                    chat_id=chat_id,
                    reply_to_message_id=main_msg.message_id
                )
                if reply_msg and hasattr(reply_msg, "message_id"):
                    sent_message_ids.append(reply_msg.message_id)
    else:
        # 📥 ДЕФОЛТНИЙ INLINE АБО СТАН БЕЗ КАРТ
        keyboard = InlineKeyboardMarkup(inline_keyboard=kb)
        chunks = notifier._split_message(text)
        for i, chunk in enumerate(chunks):
            edited_msg_id = edit_message_ids[i] if edit_message_ids and i < len(edit_message_ids) else None
            if edited_msg_id:
                try:
                    await notifier._edit_with_retry(
                        message_id=edited_msg_id,
                        text=chunk,
                        keyboard=keyboard if i == 0 else None,
                        chat_id=chat_id,
                    )
                    sent_message_ids.append(edited_msg_id)
                except Exception as ex:
                    logger.error("Failed to edit inline message chunk %d: %s", edited_msg_id, ex)
            else:
                msg = await notifier._send_with_retry(
                    chunk,
                    keyboard=keyboard if i == 0 else None,
                    disable_notification=silent,
                    chat_id=chat_id,
                )
                if msg and hasattr(msg, "message_id"):
                    sent_message_ids.append(msg.message_id)

    # ── ЗБЕРЕЖЕННЯ АЛЕРТУ В БД ДЛЯ ПЕРЕМАЛЬОВКИ ──
    if notifier._db and sent_message_ids:
        try:
            alert_dict = {
                "is_taker": True,
                "taker_mode": mode,
                "order": notifier._serialize_order(order),
            }
            if order.merchant_id:
                await notifier._db.save_sent_alert(
                    order.exchange,
                    order.merchant_id,
                    chat_id or notifier._chat_id,
                    sent_message_ids,
                    alert_dict,
                    ds,
                    False
                )
        except Exception as e:
            logger.error("Failed to save sent taker alert in DB: %s", e)

    return sent_message_ids
