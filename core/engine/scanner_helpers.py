# core/engine/scanner_helpers.py
import logging

from bot.handlers.core import is_muted
from core.engine.price_advisor import PriceAdvisor
from core.utils.tasks import spawn

logger = logging.getLogger("Scanner.Helpers")


def calculate_search_amounts(active_users: list[dict], default_amounts: list[float]) -> list[float]:
    """Будує динамічну сітку сум для пошуку на основі капіталів активних юзерів."""
    _grid: set[float] = {1000.0, 2500.0}
    for u in active_users:
        cap = float(u["capital"])
        mn = float(u.get("min_amount", 0.0)) or 1000.0
        _grid.add(mn)
        _grid.add(cap)
        _grid.add(round((mn + cap) / 2, -2))
    if not active_users:
        _grid.update(default_amounts)
    return sorted(_grid)


async def process_taker_path(
    active_users: list[dict],
    buy_grouped: dict,
    sell_grouped: dict,
    taker_scanner,
    taker_dedup,
    notifier,
    risk_engine=None,
) -> None:
    """Тейкер-шлях: алерти для TAKER_BUY / TAKER_SELL юзерів."""
    if not active_users or is_muted():
        return

    taker_users = [
        u for u in active_users
        if u.get("scanner_mode") in ("TAKER_BUY", "TAKER_SELL")
    ]
    for t_user in taker_users:
        try:
            t_orders = await taker_scanner.find_orders_for_user(
                t_user, buy_grouped, sell_grouped,
            )
            if not t_orders:
                continue
            t_mode = t_user["scanner_mode"]

            # Кандидати, які ще не відправлялись. Позначку в dedup ставимо НЕ
            # тут, а після ризик-фільтрів: раніше ордер маркувався до перевірки,
            # і якщо його відсіяв BLOCK, він лишався "побаченим" на 12 годин —
            # тобто після зняття блоку LLM юзер його вже не отримував.
            candidates = [
                o for o in t_orders
                if not taker_dedup.seen(f"taker:{t_user['user_id']}:{o.id}")
            ]

            fresh = []
            if candidates:
                if risk_engine:
                    try:
                        await risk_engine.analyze_batch_async(candidates)
                    except Exception as re_err:
                        logger.error("Error analyzing Taker orders in RiskEngine: %s", re_err)

                # Фільтруємо ордери відповідно до особистих налаштувань користувача
                filter_fop = t_user.get("filter_fop_tov", "hide")
                filter_banka = t_user.get("filter_banka_jar", "hide")

                for o in candidates:
                    risk_flags = getattr(o, "risk_flag", "") or ""
                    if filter_fop == "hide" and "FOP_TOV_BLOCKED" in risk_flags:
                        continue
                    if filter_banka == "hide" and "BANKA_JAR_BLOCKED" in risk_flags:
                        continue
                    if "BLOCK" in risk_flags:
                        continue
                    fresh.append(o)

                # Маркуємо тільки те, що реально піде юзеру.
                for o in fresh:
                    taker_dedup.mark(f"taker:{t_user['user_id']}:{o.id}")

            if fresh:
                logger.info(
                    "📤 Taker dispatch → user %s | %s | %d ордерів",
                    t_user["user_id"], t_mode, len(fresh),
                )
                spawn(
                    notifier.send_taker_to_user(t_user["chat_id"], fresh, t_mode),
                    f"taker-send-{t_user['user_id']}",
                    logger_=logger,
                )
                for o in fresh:
                    buy_ex = o.exchange if t_mode == "TAKER_BUY" else ""
                    sell_ex = o.exchange if t_mode == "TAKER_SELL" else ""
                    buy_m = o.merchant_name if t_mode == "TAKER_BUY" else ""
                    sell_m = o.merchant_name if t_mode == "TAKER_SELL" else ""
                    buy_bk = o.bank_codes[0] if o.bank_codes and t_mode == "TAKER_BUY" else ""
                    sell_bk = o.bank_codes[0] if o.bank_codes and t_mode == "TAKER_SELL" else ""
                    
                    spawn(
                        notifier._db.save_proposal(
                            buy_exchange=buy_ex,
                            sell_exchange=sell_ex,
                            buy_merchant=buy_m,
                            sell_merchant=sell_m,
                            spread_pct=0.0,
                            profit_uah=0.0,
                            deal_amount=float(o.min_limit or 0),
                            route_type=t_mode,
                            buy_bank=buy_bk,
                            sell_bank=sell_bk,
                            was_sent=True,
                            user_id=t_user["user_id"],
                        ),
                        "save_proposal_taker",
                        logger_=logger,
                    )
        except Exception as e:
            logger.warning(
                "Taker dispatch error user %s: %s",
                t_user.get("user_id"), e,
            )


async def process_maker_path(
    active_users: list[dict],
    buy_grouped: dict,
    sell_grouped: dict,
    maker_dedup,
    notifier,
) -> None:
    """Мейкер-шлях: підказки для MAKER_BUY / MAKER_SELL юзерів."""
    if not active_users or is_muted():
        return

    # Збираємо sell_book_top — найкраща (найнижча) ціна продажу зі стакану
    # Це потрібно для PriceAdvisor
    _sell_prices: list[float] = []
    for bank_code, orders in sell_grouped.items():
        for o in orders:
            try:
                _sell_prices.append(float(o.price))
            except (TypeError, ValueError):
                pass
    sell_book_top = min(_sell_prices) if _sell_prices else 0.0

    # Збираємо buy_book_top — найкраща (найвища) ціна купівлі зі стакану
    _buy_prices: list[float] = []
    for bank_code, orders in buy_grouped.items():
        for o in orders:
            try:
                _buy_prices.append(float(o.price))
            except (TypeError, ValueError):
                pass
    buy_book_top = max(_buy_prices) if _buy_prices else 0.0

    # ── MAKER_SELL: розрахунок рекомендованої ціни продажу ──
    maker_sell_users = [
        u for u in active_users
        if u.get("scanner_mode") == "MAKER_SELL"
           and float(u.get("maker_buy_price", 0)) > 0
    ]
    for ms_user in maker_sell_users:
        try:
            uid = ms_user["user_id"]
            dk = f"maker_sell:{uid}"
            if maker_dedup.seen(dk):
                continue

            buy_price = float(ms_user["maker_buy_price"])
            capital = float(ms_user["capital"])
            amount_usdt = capital / buy_price if buy_price > 0 else 100.0

            advice = PriceAdvisor.suggest_sell_price(
                buy_price=buy_price,
                amount_usdt=amount_usdt,
                min_margin=float(ms_user.get("target_margin", 0.003)),
            )

            # Перевіряємо: чи sell_book_top вигідний для мейкера
            if sell_book_top > 0:
                advice["sell_book_top"] = sell_book_top
                advice["book_vs_min"] = round(sell_book_top - advice["min_sell_price"], 4)

            maker_dedup.mark(dk)
            logger.info(
                "📤 Maker SELL advice → user %s | buy=%.2f min_sell=%.4f book_top=%.2f",
                uid, buy_price, advice["min_sell_price"], sell_book_top,
            )
            spawn(
                notifier.send_maker_sell_update(ms_user["chat_id"], advice),
                f"maker-sell-{uid}",
                logger_=logger,
            )
            spawn(
                notifier._db.save_proposal(
                    buy_exchange="",
                    sell_exchange="",
                    buy_merchant="",
                    sell_merchant="",
                    spread_pct=float(ms_user.get("target_margin", 0.003)) * 100,
                    profit_uah=float(advice.get("expected_profit_uah", 0.0) or 0.0),
                    deal_amount=capital,
                    route_type="MAKER_SELL",
                    buy_bank="",
                    sell_bank="",
                    was_sent=True,
                    user_id=uid,
                ),
                "save_proposal_maker",
                logger_=logger,
            )
        except Exception as e:
            logger.warning("Maker SELL error user %s: %s", ms_user.get("user_id"), e)

    # ── MAKER_BUY: аналіз ринку + рекомендація ціни купівлі ──
    maker_buy_users = [
        u for u in active_users
        if u.get("scanner_mode") == "MAKER_BUY"
    ]
    for mb_user in maker_buy_users:
        try:
            uid = mb_user["user_id"]
            dk = f"maker_buy:{uid}"
            if maker_dedup.seen(dk):
                continue

            if sell_book_top <= 0:
                continue  # Немає даних по стакану

            target_margin = float(mb_user.get("target_margin", 0.005))
            capital = float(mb_user["capital"])

            advice = PriceAdvisor.suggest_buy_price(
                sell_book_top=sell_book_top,
                amount_usdt=capital / sell_book_top if sell_book_top > 0 else 100.0,
                target_margin=target_margin,
            )

            # Додаємо контекст стакану
            if buy_book_top > 0:
                advice["buy_book_top"] = buy_book_top

            maker_dedup.mark(dk)
            logger.info(
                "📤 Maker BUY advice → user %s | sell_top=%.2f max_buy=%.4f margin=%.1f%%",
                uid, sell_book_top, advice["max_buy_price"], target_margin * 100,
            )
            spawn(
                notifier.send_maker_buy_suggestion(mb_user["chat_id"], advice),
                f"maker-buy-{uid}",
                logger_=logger,
            )
            spawn(
                notifier._db.save_proposal(
                    buy_exchange="",
                    sell_exchange="",
                    buy_merchant="",
                    sell_merchant="",
                    spread_pct=target_margin * 100,
                    profit_uah=float(advice.get("expected_profit_uah", 0.0) or 0.0),
                    deal_amount=capital,
                    route_type="MAKER_BUY",
                    buy_bank="",
                    sell_bank="",
                    was_sent=True,
                    user_id=uid,
                ),
                "save_proposal_maker",
                logger_=logger,
            )
        except Exception as e:
            logger.warning("Maker BUY error user %s: %s", mb_user.get("user_id"), e)
