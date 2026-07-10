import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from html import escape
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from exchanges.base import Order
from core.engine.network_fee_engine import NetworkFeeEngine
from core.analytics.merchant_profile import build_profile_url, build_app_profile_url, build_android_intent_profile_url
from bot.handlers import core as bot_commands
from bot.formatters import (
    EXCHANGE_ICONS,
    BANKS_SHORT,
    _bank_code_to_db,
    rec_badge,
    _llm_verdict_block,
    _terms_block,
    _format_bank_list,
    _format_route_variants,
    _profile_link,
    _verified_badge,
    _alert_grade,
    _regex_warn_block,
    _risk_badge,
)

logger = logging.getLogger(__name__)


@dataclass
class SpreadAlert:
    buy_order: Order
    sell_order: Order
    spread_pct: float
    profit_uah: float
    deal_amount_uah: float
    buy_bank: str
    sell_bank: str
    buy_banks_all: list[str] | None = None
    sell_banks_all: list[str] | None = None
    buy_banks_fit: list[str] | None = None
    sell_banks_fit: list[str] | None = None
    route_variants: list[str] | None = None
    route_type: str = "UNKNOWN"
    route_pairs: list[list[str]] | None = None
    # 🚀 ДОДАНО ПОЛЯ ДЛЯ LLM
    buy_rec: str = "PENDING"
    sell_rec: str = "PENDING"
    buy_reason: str = ""
    sell_reason: str = ""
    buy_terms_summary: str = ""
    sell_terms_summary: str = ""
    buy_reviews_analysis: str = ""
    sell_reviews_analysis: str = ""
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


def _online_badge(order: Order) -> str:
    last_online = getattr(order, "last_online_mins", None)
    if last_online is None:
        return ""
    if last_online <= 2:
        return f" (🟢 {last_online}m)" if last_online > 0 else " (🟢 online)"
    else:
        return f" (🟡 {last_online}m)"


async def send_single(
    notifier,
    alert: SpreadAlert,
    chat_id: int | None = None,
    display_settings: dict | None = None,
    is_sniper_match: bool = False,
    edit_message_ids: list[int] | None = None,
) -> list[int]:
    # Display settings (per-user)
    ds = display_settings or {
        "show_ai_terms_summary": True, "show_full_terms": True,
        "show_ai_logic": True, "show_bank_details": True, "show_llm_summary": True,
    }

    # 🔄 Refresh LLM verdicts from DB (LLM може завершитись після створення алерту)
    if notifier._db:
        try:
            b_rec, b_verdict, b_reason, b_terms, b_rev = await notifier._db.get_trade_recommendation_full(
                alert.buy_order.exchange, alert.buy_order.merchant_id
            )
            s_rec, s_verdict, s_reason, s_terms, s_rev = await notifier._db.get_trade_recommendation_full(
                alert.sell_order.exchange, alert.sell_order.merchant_id
            )
            alert.buy_rec = b_rec
            alert.sell_rec = s_rec
            alert.buy_reason = b_reason
            alert.sell_reason = s_reason
            alert.buy_terms_summary = b_terms
            alert.sell_terms_summary = s_terms
            alert.buy_reviews_analysis = b_rev
            alert.sell_reviews_analysis = s_rev

            # Також підвантажуємо актуальні відгуки та оновлюємо risk_flag і stats
            b_rev_sum = await notifier._db.get_reviews_summary(alert.buy_order.exchange, alert.buy_order.merchant_id)
            s_rev_sum = await notifier._db.get_reviews_summary(alert.sell_order.exchange, alert.sell_order.merchant_id)

            from core.engine.risk_engine import _build_review_flags_from_summary

            for order, rec, verdict, reason, rev_sum in [
                (alert.buy_order, b_rec, b_verdict, b_reason, b_rev_sum),
                (alert.sell_order, s_rec, s_verdict, s_reason, s_rev_sum),
            ]:
                flags = []
                # 1. Додаємо статус перевірки/вердикту LLM
                if rec == "RECHECKING":
                    flags.append("LLM_PENDING:RECHECK")
                elif rec == "PENDING" or not verdict:
                    flags.append("LLM_PENDING")
                elif verdict == "BLOCK":
                    flags.append(f"BLOCK:LLM_BLOCK:{reason or 'block'}")
                elif verdict == "SUSPICIOUS":
                    flags.append("LLM_SUSPICIOUS")
                elif verdict == "UNKNOWN":
                    flags.append("LLM_UNKNOWN")
                
                # 2. Додаємо прапори відгуків з бази
                if rev_sum:
                    rev_flags = _build_review_flags_from_summary(rev_sum)
                    flags.extend(rev_flags)
                    
                    pos = int(rev_sum.get("positive", 0) or 0)
                    neg = int(rev_sum.get("negative", 0) or 0)
                    neutral = int(rev_sum.get("neutral", 0) or 0)
                    total = pos + neg + neutral
                    if total > 0:
                        order.review_neg_pct = (neg / total) * 100.0
                    else:
                        order.review_neg_pct = 0.0
                    order.review_fetched = True
                
                # 3. Зберігаємо існуючі не-LLM і не-відгукові прапори з оригінального risk_flag
                orig_flags = [f.strip() for f in getattr(order, "risk_flag", "").split(",") if f.strip()]
                for f in orig_flags:
                    is_llm_flag = (
                        f.startswith("LLM_PENDING") or 
                        f.startswith("BLOCK") or 
                        f.startswith("LLM_SUSPICIOUS") or 
                        f.startswith("LLM_UNKNOWN") or
                        f == "OK" or 
                        f == "PENDING"
                    )
                    is_review_flag = (
                        f.startswith("NEEDS_LLM:BADREVIEWS") or
                        f.startswith("BADREVIEWS_TEXTS") or
                        f.startswith("BADREVIEWS") or
                        f.startswith("REVIEW_SOFT") or
                        f.startswith("REVIEW_UNFLAGGED")
                    )
                    if not (is_llm_flag or is_review_flag):
                        flags.append(f)
                
                # Записуємо очищені дедубльовані прапори
                unique_flags = []
                for f in flags:
                    if f not in unique_flags:
                        unique_flags.append(f)
                
                # 4. Припасовуємо прапори під персональні налаштування відображення юзера (hide/warn/show)
                filter_fop_tov = ds.get("filter_fop_tov", "hide")
                filter_banka_jar = ds.get("filter_banka_jar", "hide")
                
                final_flags = []
                for f in unique_flags:
                    if "FOP_TOV_BLOCKED" in f:
                        if filter_fop_tov == "hide":
                            final_flags.append("BLOCK:FOP_TOV_BLOCKED")
                        elif filter_fop_tov == "warn":
                            final_flags.append("FOP_TOV_WARN")
                    elif "BANKA_JAR_BLOCKED" in f:
                        if filter_banka_jar == "hide":
                            final_flags.append("BLOCK:BANKA_JAR_BLOCKED")
                        elif filter_banka_jar == "warn":
                            final_flags.append("BANKA_JAR_WARN")
                    else:
                        final_flags.append(f)
                
                order.risk_flag = ",".join(final_flags) if final_flags else "OK"

        except Exception as e:
            logger.error("Error refreshing LLM verdicts inside send_single: %s", e)

    title, silent = _alert_grade(alert.spread_pct)
    if is_sniper_match:
        title = f"🎯 <b>СНАЙПЕР ОРДЕР!</b>\n{title}"
        silent = False  # Примусово вмикаємо звук для снайпера

    b_icon = EXCHANGE_ICONS.get(alert.buy_order.exchange, "◽️")
    s_icon = EXCHANGE_ICONS.get(alert.sell_order.exchange, "◽️")

    buy_name = _profile_link(
        alert.buy_order.exchange,
        alert.buy_order.merchant_id,
        alert.buy_order.merchant_name,
        side="buy",
    )
    sell_name = _profile_link(
        alert.sell_order.exchange,
        alert.sell_order.merchant_id,
        alert.sell_order.merchant_name,
        side="sell",
    )

    buy_risk = _risk_badge(alert.buy_order)
    sell_risk = _risk_badge(alert.sell_order)

    route_type = getattr(alert, "route_type", "")
    if route_type == "CROSS":
        route_marker = "🔀 CROSS"
    elif route_type == "INTRA":
        route_marker = "🔁 INTRA"
    else:
        route_marker = "📍 ROUTE"

    route_variants = _format_route_variants(getattr(alert, "route_variants", None))
    buy_all = _format_bank_list(getattr(alert, "buy_banks_all", None))
    sell_all = _format_bank_list(getattr(alert, "sell_banks_all", None))
    buy_fit = _format_bank_list(getattr(alert, "buy_banks_fit", None))
    sell_fit = _format_bank_list(getattr(alert, "sell_banks_fit", None))
    buy_warn = _regex_warn_block(alert.buy_order)
    sell_warn = _regex_warn_block(alert.sell_order)

    buy_online = _online_badge(alert.buy_order)
    sell_online = _online_badge(alert.sell_order)
    buy_name_str = f"{rec_badge(alert.buy_rec)} {buy_name}{_verified_badge(alert.buy_order)}{buy_online}"
    sell_name_str = f"{rec_badge(alert.sell_rec)} {sell_name}{_verified_badge(alert.sell_order)}{sell_online}"

    # 🧠 LLM Verdict блоки (конфігуровані per-user)
    if ds.get("show_llm_summary", True):
        buy_llm = _llm_verdict_block(
            "Buy", alert.buy_rec, alert.buy_reason,
            terms_summary=getattr(alert, "buy_terms_summary", ""),
            show_ai_logic=ds.get("show_ai_logic", True),
            show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
            reviews_analysis=getattr(alert, "buy_reviews_analysis", "")
        )
        sell_llm = _llm_verdict_block(
            "Sell", alert.sell_rec, alert.sell_reason,
            terms_summary=getattr(alert, "sell_terms_summary", ""),
            show_ai_logic=ds.get("show_ai_logic", True),
            show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
            reviews_analysis=getattr(alert, "sell_reviews_analysis", "")
        )
    else:
        buy_llm = _llm_verdict_block("Buy", alert.buy_rec, "", show_ai_logic=False, show_ai_terms_summary=False)
        sell_llm = _llm_verdict_block("Sell", alert.sell_rec, "", show_ai_logic=False, show_ai_terms_summary=False)

    # 🚀 Блок D: Мережі переказу
    buy_ex = alert.buy_order.exchange
    sell_ex = alert.sell_order.exchange
    if buy_ex != sell_ex:
        net_block = NetworkFeeEngine.format_for_alert(buy_ex, sell_ex)
        net_block_html = "\n" + escape(net_block) + "\n"
    else:
        net_block_html = ""

    # 🚀 ЕКСПЕРИМЕНТ: Генеруємо підстроку з дужками-деталями інвентарю для ТГ
    asym_str = ""
    if getattr(alert, "is_asymmetric", False) and getattr(alert, "asymmetric_details", None):
        asym = alert.asymmetric_details
        asym_str = (
            f"\n  └ <i>(Деталі: Купівля: {asym['buy_required']:.0f}₴ | "
            f"Продаж: {asym['sell_executed']:.0f}₴ | "
            f"Інвентар: +{asym['inventory_usdt']:.2f} USDT)</i>"
        )

    buy_rate = float(alert.buy_order.price) if float(alert.buy_order.price) > 0 else 1.0
    sell_rate = float(alert.sell_order.price) if float(alert.sell_order.price) > 0 else 1.0
    buy_deal_usdt = alert.deal_amount_uah / buy_rate
    buy_min_usdt = float(alert.buy_order.min_limit) / buy_rate
    buy_max_usdt = float(alert.buy_order.max_limit) / buy_rate
    sell_min_usdt = float(alert.sell_order.min_limit) / sell_rate
    sell_max_usdt = float(alert.sell_order.max_limit) / sell_rate

    text = (
        f"{title}\n\n"
        f"💰 Профіт: <b>+{alert.profit_uah:.2f} ₴</b>   "
        f"💼 Угода: <b>{alert.deal_amount_uah:.0f} ₴</b> <i>(~{buy_deal_usdt:.1f} USDT)</i>{asym_str}\n"  # ← Інжектовано деталі фічі
        f"🔄 Маршрут: {route_marker} | "
        f"{b_icon}{escape(alert.buy_order.exchange)} → "
        f"{s_icon}{escape(alert.sell_order.exchange)}\n"
        f"⏱ {alert.timestamp.strftime('%H:%M:%S')}\n"
        f"📈 Спред: <b>{alert.spread_pct:.2f}%</b>"
        f"{net_block_html}\n"
    )

    # 🔘 Деталі банків (під спойлером, конфігуровано)
    if ds.get("show_bank_details", True):
        text += (
            f"<blockquote expandable>"
            f"🏦 Варіанти зв'язки: {route_variants}\n"
            f"🛒 Buy банки: {buy_all}\n"
            f"✅ Buy фільтр: {buy_fit}\n"
            f"💸 Sell банки: {sell_all}\n"
            f"✅ Sell фільтр: {sell_fit}"
            f"</blockquote>\n"
        )

    # ── КУПУЄМО ──
    buy_terms_raw = getattr(alert.buy_order, "trade_terms", "") or ""
    text += (
        f"🛒 <b>КУПУЄМО</b>\n"
        f"Курс: <code>{escape(str(alert.buy_order.price))}</code>\n"
        f"Мерчант: {buy_name_str} "
        f"({alert.buy_order.finish_rate_pct:.1f}% | {alert.buy_order.month_order_count} угод)\n"
        f"Ліміти: <code>{escape(str(alert.buy_order.min_limit))}–{escape(str(alert.buy_order.max_limit))} ₴</code> <i>(~{buy_min_usdt:.1f}–{buy_max_usdt:.1f} USDT)</i>"
        f"  💎 <code>{float(alert.buy_order.available_amount):.2f} USDT</code> в ордері\n"
        f"{buy_risk if buy_risk else ''}"
        f"{buy_warn if buy_warn else ''}"
    )
    buy_terms_blk = _terms_block(
        buy_terms_raw,
        terms_summary=alert.buy_terms_summary,
        show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
        show_full_terms=ds.get("show_full_terms", True),
    )
    if buy_terms_blk:
        text += buy_terms_blk
    text += f"\n{buy_llm}\n"

    # ── ПРОДАЄМО ──
    sell_terms_raw = getattr(alert.sell_order, "trade_terms", "") or ""
    text += (
        f"💸 <b>ПРОДАЄМО</b>\n"
        f"Курс: <code>{escape(str(alert.sell_order.price))}</code>\n"
        f"Мерчант: {sell_name_str} "
        f"({alert.sell_order.finish_rate_pct:.1f}% | {alert.sell_order.month_order_count} угод)\n"
        f"Ліміти: <code>{escape(str(alert.sell_order.min_limit))}–{escape(str(alert.sell_order.max_limit))} ₴</code> <i>(~{sell_min_usdt:.1f}–{sell_max_usdt:.1f} USDT)</i>"
        f"  💎 <code>{float(alert.sell_order.available_amount):.2f} USDT</code> в ордері\n"
        f"{sell_risk if sell_risk else ''}"
        f"{sell_warn if sell_warn else ''}"
    )
    sell_terms_blk = _terms_block(
        sell_terms_raw,
        terms_summary=alert.sell_terms_summary,
        show_ai_terms_summary=ds.get("show_ai_terms_summary", True),
        show_full_terms=ds.get("show_full_terms", True),
    )
    if sell_terms_blk:
        text += sell_terms_blk
    text += f"\n{sell_llm}"

    # 🚀 НОВІ ІНТЕРАКТИВНІ КНОПКИ
    kb = []

    b_ad = getattr(alert.buy_order, "ad_id", getattr(alert.buy_order, "order_id", ""))
    s_ad = getattr(alert.sell_order, "ad_id", getattr(alert.sell_order, "order_id", ""))

    if b_ad and s_ad and alert.buy_rec != "REJECT" and alert.sell_rec != "REJECT":
        cache_key = f"{b_ad[:12]}_{s_ad[:12]}"

        bot_commands._spread_cache[cache_key] = (alert, time.time())

        kb.append([
            InlineKeyboardButton(text=f"⚡ Авто-Трейд (T→T) {alert.spread_pct:.2f}%",
                                 callback_data=f"trade:tt:{cache_key}")
        ])

        if ds.get("is_hybrid_routes_enabled", False):
            kb.append([
                InlineKeyboardButton(text=f"🚨 T→M (Купити і стати мейкером)", callback_data=f"trade:tm:{cache_key}")
            ])
            kb.append([
                InlineKeyboardButton(text=f"🚨 M→T (Стати мейкером і злити)", callback_data=f"trade:mt:{cache_key}")
            ])

    # Кнопки Single-Leg
    single_leg_row = []
    if b_ad and alert.buy_rec != "REJECT":
        buy_bank = alert.buy_bank or ""
        sl_buy_key = f"{b_ad[:10]}|{alert.buy_order.exchange[:3]}|{buy_bank[:4]}"
        bot_commands._single_leg_cache[f"b:{sl_buy_key}"] = {
            "ad_id": str(b_ad), "exchange": alert.buy_order.exchange,
            "price": float(alert.buy_order.price),
            "merchant_id": alert.buy_order.merchant_id,
            "min_limit": float(alert.buy_order.min_limit),
            "max_limit": float(alert.buy_order.max_limit),
            "bank": buy_bank, "ts": time.time(),
        }
        single_leg_row.append(
            InlineKeyboardButton(
                text=f"🛒 Купити ({alert.buy_order.exchange})",
                callback_data=f"sl:b:{sl_buy_key}"
            )
        )
    if s_ad and alert.sell_rec != "REJECT":
        sell_bank = alert.sell_bank or ""
        sl_sell_key = f"{s_ad[:10]}|{alert.sell_order.exchange[:3]}|{sell_bank[:4]}"
        bot_commands._single_leg_cache[f"s:{sl_sell_key}"] = {
            "ad_id": str(s_ad), "exchange": alert.sell_order.exchange,
            "price": float(alert.sell_order.price),
            "merchant_id": alert.sell_order.merchant_id,
            "min_limit": float(alert.sell_order.min_limit),
            "max_limit": float(alert.sell_order.max_limit),
            "bank": sell_bank, "ts": time.time(),
        }
        single_leg_row.append(
            InlineKeyboardButton(
                text=f"💸 Продати ({alert.sell_order.exchange})",
                callback_data=f"sl:s:{sl_sell_key}"
            )
        )
    if single_leg_row:
        kb.append(single_leg_row)

    buy_url = getattr(alert.buy_order, "link", "") or build_profile_url(
        alert.buy_order.exchange, alert.buy_order.merchant_id
    )
    sell_url = getattr(alert.sell_order, "link", "") or build_profile_url(
        alert.sell_order.exchange, alert.sell_order.merchant_id
    )
    url_row = []
    if buy_url:
        url_row.append(InlineKeyboardButton(text="🔗 Buy на біржі", url=buy_url))
    if sell_url:
        url_row.append(InlineKeyboardButton(text="🔗 Sell на біржі", url=sell_url))
    if url_row:
        kb.append(url_row)

    # 📱 Додаємо окремі кнопки переходу в додатки для Buy і Sell
    buy_ios = build_app_profile_url(alert.buy_order.exchange, alert.buy_order.merchant_id)
    buy_android = build_android_intent_profile_url(alert.buy_order.exchange, alert.buy_order.merchant_id, side="buy")
    buy_app_row = []
    if buy_ios:
        buy_app_row.append(InlineKeyboardButton(text="📱 Buy iOS App", url=buy_ios))
    if buy_android:
        buy_app_row.append(InlineKeyboardButton(text="🤖 Buy Android App", url=buy_android))
    if buy_app_row:
        kb.append(buy_app_row)

    sell_ios = build_app_profile_url(alert.sell_order.exchange, alert.sell_order.merchant_id)
    sell_android = build_android_intent_profile_url(alert.sell_order.exchange, alert.sell_order.merchant_id, side="sell")
    sell_app_row = []
    if sell_ios:
        sell_app_row.append(InlineKeyboardButton(text="📱 Sell iOS App", url=sell_ios))
    if sell_android:
        sell_app_row.append(InlineKeyboardButton(text="🤖 Sell Android App", url=sell_android))
    if sell_app_row:
        kb.append(sell_app_row)

    b_mid = alert.buy_order.merchant_id
    if b_mid:
        kb.append([
            InlineKeyboardButton(text="🔴 Buy: 3-ті",
                                 callback_data=f"fb:{alert.buy_order.exchange}:{b_mid}:triangle"),
            InlineKeyboardButton(text="🔴 Чек", callback_data=f"fb:{alert.buy_order.exchange}:{b_mid}:receipt"),
            InlineKeyboardButton(text="🔴 ТГ", callback_data=f"fb:{alert.buy_order.exchange}:{b_mid}:chat"),
        ])

    s_mid = alert.sell_order.merchant_id
    if s_mid:
        kb.append([
            InlineKeyboardButton(text="🔵 Sell: 3-ті",
                                 callback_data=f"fb:{alert.sell_order.exchange}:{s_mid}:triangle"),
            InlineKeyboardButton(text="🔵 Чек", callback_data=f"fb:{alert.sell_order.exchange}:{s_mid}:receipt"),
            InlineKeyboardButton(text="🔵 ТГ", callback_data=f"fb:{alert.sell_order.exchange}:{s_mid}:chat"),
        ])

    # ── КАРТКОВИЙ БЛОК З ДИНАМІЧНИМ ПРОРАХУНКОМ АСИМЕТРІЇ ──
    card_text_combined = ""
    buy_card_rows = []
    sell_card_rows = []
    output_mode = "inline"

    if getattr(notifier, "card_notifier", None):
        # Читаємо словник конфігурації карткової таблиці
        card_settings = await notifier._db.get_user_card_settings(chat_id or notifier._chat_id) or {}
        output_mode = card_settings.get("card_output_mode", "inline")

        b_ad_id = getattr(alert.buy_order, "ad_id", getattr(alert.buy_order, "order_id", ""))
        s_ad_id = getattr(alert.sell_order, "ad_id", getattr(alert.sell_order, "order_id", ""))

        # Розумний розподіл сум угоди
        buy_target = alert.deal_amount_uah
        if getattr(alert, "is_asymmetric", False) and getattr(alert, "asymmetric_details", None):
            sell_target = alert.asymmetric_details["sell_executed"]
        else:
            sell_target = alert.deal_amount_uah + alert.profit_uah

        # Прораховуємо BUY ногу
        buy_card_text, buy_card_rows, buy_card_obj = await notifier.card_notifier.get_card_block(
            chat_id=chat_id or notifier._chat_id,
            target_amount=buy_target,
            direction="buy",
            bank=_bank_code_to_db(alert.buy_bank or ""),
            order_id=str(b_ad_id) if b_ad_id else "",
        )

        # Прораховуємо SELL ногу
        buy_card_id = None
        if buy_card_obj:
            buy_card_id = buy_card_obj.get("id")
        else:
            try:
                owner_uid = chat_id or notifier._chat_id
                buy_bank_db = _bank_code_to_db(alert.buy_bank or "")
                buy_cards = await notifier._db.get_cards(owner_id=owner_uid, bank_name=buy_bank_db, status="active")
                if buy_cards:
                    buy_card_id = buy_cards[0].get("id")
            except Exception as e:
                logger.warning("Error fetching fallback buy_card_id: %s", e)
        sell_card_text, sell_card_rows, _ = await notifier.card_notifier.get_card_block(
            chat_id=chat_id or notifier._chat_id,
            target_amount=sell_target,
            direction="sell",
            bank=_bank_code_to_db(alert.sell_bank or ""),
            order_id=str(s_ad_id) if s_ad_id else "",
            buy_card_spent_fiat=buy_target,
            buy_card_id=buy_card_id
        )

        # Зшиваємо результати через очищення від None/порожнечі
        card_text_combined = "\n".join(filter(None, [buy_card_text, sell_card_text]))

        # Модифікуємо текст і кнопки ТІЛЬКИ якщо обрано старий inline-режим
        if card_text_combined and output_mode == "inline":
            text += f"\n{card_text_combined}"
            kb.extend(buy_card_rows)
            if sell_card_rows != buy_card_rows:
                kb.extend(sell_card_rows)

    sent_message_ids = []

    # ── ФІНАЛЬНИЙ ПУШ У TELEGRAM З ПІДТРИМКОЮ SINGLE-LEG REPLY-ВІДПОВІДЕЙ ──
    if card_text_combined and output_mode == "reply":
        # 🔀 РЕЖИМ REPLY: Відокремлюємо карти. Текст спреду лишається ідеально чистим.
        main_keyboard = InlineKeyboardMarkup(inline_keyboard=kb)

        # Збираємо унікальні кнопки карт
        combined_card_rows = buy_card_rows + [r for r in sell_card_rows if r not in buy_card_rows]
        card_keyboard = InlineKeyboardMarkup(inline_keyboard=combined_card_rows)

        # 1. Надсилаємо базовий алерт чистого спреду
        main_chunks = notifier._split_message(text)
        main_msg = None

        for i, chunk in enumerate(main_chunks):
            # Check if we should edit instead of send
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
                        # set main_msg reference with message_id to reply to it
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

        # 2. Якщо алерт пройшов — стріляємо реплаєм з картою
        if main_msg and hasattr(main_msg, "message_id"):
            card_text_header = "💳 <b>Рекомендований пластик під угоду:</b>\n\n"
            reply_chunk = f"{card_text_header}{card_text_combined}"
            
            edited_reply_msg_id = edit_message_ids[len(main_chunks)] if edit_message_ids and len(main_chunks) < len(edit_message_ids) else None
            if edited_reply_msg_id:
                try:
                    await notifier._edit_with_retry(
                        message_id=edited_reply_msg_id,
                        text=reply_chunk,
                        keyboard=card_keyboard,
                        chat_id=chat_id,
                    )
                    sent_message_ids.append(edited_reply_msg_id)
                except Exception as ex:
                    logger.error("Failed to edit reply message %d: %s", edited_reply_msg_id, ex)
            else:
                reply_msg = await notifier._send_with_retry(
                    reply_chunk,
                    keyboard=card_keyboard,
                    disable_notification=silent,
                    chat_id=chat_id,
                    reply_to_message_id=main_msg.message_id
                )
                if reply_msg and hasattr(reply_msg, "message_id"):
                    sent_message_ids.append(reply_msg.message_id)

    else:
        # 📥 ДЕФОЛТНИЙ INLINE РЕЖИМ (Або випадок, коли взагалі немає підходящих карток)
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
                    
    return sent_message_ids


async def send_batch(notifier, batch: list[SpreadAlert], chat_id: int | None = None) -> None:
    """Підсумок усіх знайдених маршрутів за цикл."""
    medals = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = [
        f"📋 <b>ПІДСУМОК: АКТУАЛЬНІ МАРШРУТИ</b>  "
        f"<code>[{batch[0].timestamp.strftime('%H:%M:%S')}]</code>\n"
        f"<code>{'─' * 28}</code>\n"
    ]

    for i, a in enumerate(batch[:10]):
        b_icon = EXCHANGE_ICONS.get(a.buy_order.exchange, "◽️")
        s_icon = EXCHANGE_ICONS.get(a.sell_order.exchange, "◽️")
        b_short = BANKS_SHORT.get(a.buy_bank, a.buy_bank)
        s_short = BANKS_SHORT.get(a.sell_bank, a.sell_bank)

        risks = _risk_badge(a.buy_order, short=True) + _risk_badge(a.sell_order, short=True)
        warns = _regex_warn_block(a.buy_order, short=True) + _regex_warn_block(a.sell_order, short=True)
        chips = (risks + warns).strip()

        b_online = _online_badge(a.buy_order)
        s_online = _online_badge(a.sell_order)
        b_nick = f"{_profile_link(a.buy_order.exchange, a.buy_order.merchant_id, a.buy_order.merchant_name, side='buy')}{b_online}"
        s_nick = f"{_profile_link(a.sell_order.exchange, a.sell_order.merchant_id, a.sell_order.merchant_name, side='sell')}{s_online}"

        buy_links = f"<a href='{a.buy_order.link}'>Купити</a>"
        sell_links = f"<a href='{a.sell_order.link}'>Продати</a>"

        lines.append(
            f"{medals[i]} <b>{a.spread_pct:.2f}%</b>  "
            f"<code>+{a.profit_uah:.0f} ₴</code>"
            f"{'  ' + chips if chips else ''}\n"
            f"  {b_icon} {b_nick} <code>{a.buy_order.price}</code> {b_short}\n"
            f"  {s_icon} {s_nick} <code>{a.sell_order.price}</code> {s_short}\n"
            f"  📐 <code>{a.buy_order.min_limit}–{a.buy_order.max_limit}</code> "
            f"→ <code>{a.sell_order.min_limit}–{a.sell_order.max_limit} ₴</code>\n"
            f"  {buy_links}  ·  "
            f"{sell_links}\n"
            f"<code>{'─' * 28}</code>\n"
        )

    text = "".join(lines)
    for chunk in notifier._split_message(text):
        await notifier._send_with_retry(chunk, chat_id=chat_id)
