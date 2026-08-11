# core/engine/scanner_helpers.py
import logging

from bot.handlers.core import is_muted
from core.engine.price_advisor import PriceAdvisor
from core.utils.tasks import spawn
from core.engine import risk_flags as risk_flags_mod
from core.engine.risk_decision import decide
from core.risk.policy import SIDE_BUY, SIDE_SELL

logger = logging.getLogger("Scanner.Helpers")


def _user_modes(user: dict) -> list[str]:
    """
    Активні режими користувача.

    scanner_modes заповнює UserRepo (див. _parse_scanner_modes) і там уже
    врахований фолбек на одиничний scanner_mode. Але сюди приходять і
    словники, зібрані руками — у тестах і в HTTP-шарі, — тож підстраховка
    лишається тут.
    """
    modes = user.get("scanner_modes")
    if isinstance(modes, (list, tuple)) and modes:
        return [str(m).upper() for m in modes]
    return [str(user.get("scanner_mode") or "SPREAD").upper()]


# Дайджест «ордери були, але картки не тягнуть» — не частіше разу на 6 годин
# на кожну (людину, режим, причину). Причина в ключі навмисне: коли змінилась
# причина, ситуація справді інша, і про це варто сказати. Коли не змінилась —
# людина вже все зрозуміла з першого разу.
_last_rejection_digest: dict[tuple, float] = {}
_REJECTION_DIGEST_TTL = 6 * 3600


async def _notify_all_rejected(notifier, user: dict, mode: str, rejections: list,
                               show_rejected: str = "with_reason") -> None:
    """
    Пояснює порожній прохід, коли причина — картки, а не ринок.

    Досі порожній результат виглядав однаково і коли ордерів справді немає,
    і коли їх десяток, але жоден не проходить через ліміти карток. Різниця
    для людини принципова: у першому випадку робити нічого, у другому —
    поповнити картку або підняти ліміт.

    show_rejected='hide' вимикає це повідомлення повністю: користувач так
    вирішив у налаштуваннях, статистика в /card_rejections лишається.
    """
    if not rejections or show_rejected == "hide":
        return

    import time
    from collections import Counter
    from core.engine import rejection_codes as rc

    counts = Counter(r.code for r in rejections)
    top_code, top_hits = counts.most_common(1)[0]

    key = (user.get("user_id"), mode, top_code)
    now = time.time()
    if now - _last_rejection_digest.get(key, 0.0) < _REJECTION_DIGEST_TTL:
        return
    _last_rejection_digest[key] = now

    # Найдорожчі відмови — ті, де видно, скільки саме не вистачило.
    ranked = sorted(rejections, key=lambda r: -r.shortfall_uah)[:5]
    lines = []
    for r in ranked:
        price = f"{r.price:.2f}".rstrip("0").rstrip(".")
        lines.append(f"  ├ <i>{r.merchant_name}</i> · {price} ₴ — {r.reason}")
    if len(rejections) > len(ranked):
        lines.append(f"  └ <i>…і ще {len(rejections) - len(ranked)}</i>")

    text = (
        f"🔍 <b>{mode}: ордери є, але жоден не проходить по картках</b>\n\n"
        f"Перевірено {len(rejections)}, головна причина — "
        f"<b>{rc.label(top_code).lower()}</b> ({top_hits}).\n\n"
        + "\n".join(lines)
        + "\n\n<i>Розклад за тиждень — /card_rejections. "
          "Вимкнути ці повідомлення — у налаштуваннях карток.</i>"
    )

    chat_id = user.get("chat_id") or user.get("user_id")
    try:
        await notifier.send_plain(chat_id, text)
    except Exception as e:
        logger.debug("Не вдалось надіслати дайджест причин відмов: %s", e)


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

    # Режимів у користувача може бути кілька — наприклад, і купівля, і
    # продаж одночасно. Раніше тут стояла перевірка одного scanner_mode,
    # тому «ловити обидві сторони» доводилось імітувати перемиканням режиму
    # туди-сюди, і половину часу друга сторона просто не сканувалась.
    #
    # Розгортаємо в пари (юзер, режим): кожна проходить свій набір фільтрів
    # (taker_buy_* проти taker_sell_*) і має власний дедуп.
    taker_jobs: list[tuple[dict, str]] = [
        (u, mode)
        for u in active_users
        for mode in _user_modes(u)
        if mode in ("TAKER_BUY", "TAKER_SELL")
    ]

    for t_user, t_mode in taker_jobs:
        try:
            scan = await taker_scanner.scan(
                {**t_user, "scanner_mode": t_mode}, buy_grouped, sell_grouped,
            )
            t_orders = scan.orders

            # Причини пишемо завжди — і коли ордери знайшлись, і коли ні.
            # Порожній прохід виглядав як «ринку немає», хоча ордери були,
            # просто картки не тягнули; а прохід, де автоскейл ужав обсяг,
            # не лишав узагалі нічого, хоча саме він і показує, чого коштує
            # стеля одного банку.
            if scan.logged and getattr(taker_scanner, "db", None):
                try:
                    await taker_scanner.db.log_rejections(
                        t_user["user_id"], t_mode, scan.logged
                    )
                except Exception as log_err:
                    logger.debug("Не вдалось записати причини відмов: %s", log_err)

            if not t_orders:
                show_rejected = "with_reason"
                if getattr(taker_scanner, "db", None):
                    card_settings = await taker_scanner.db.get_user_card_settings(
                        t_user["user_id"]
                    ) or {}
                    show_rejected = card_settings.get("show_rejected_orders") or "with_reason"
                await _notify_all_rejected(
                    notifier, t_user, t_mode, scan.rejections, show_rejected
                )
                continue

            # Кандидати, які ще не відправлялись. Позначку в dedup ставимо НЕ
            # тут, а після ризик-фільтрів: раніше ордер маркувався до перевірки,
            # і якщо його відсіяв BLOCK, він лишався "побаченим" на 12 годин —
            # тобто після зняття блоку LLM юзер його вже не отримував.
            candidates = [
                o for o in t_orders
                if not taker_dedup.seen(f"taker:{t_user['user_id']}:{t_mode}:{o.id}")
            ]

            fresh = []
            if candidates:
                if risk_engine:
                    try:
                        await risk_engine.analyze_batch_async(candidates)
                    except Exception as re_err:
                        logger.error("Error analyzing Taker orders in RiskEngine: %s", re_err)

                # Персональна політика. Напрямок у тейкері один на весь
                # прохід і береться з РЕЖИМУ: у стакані `order.side` означає
                # бік мерчанта, а не користувача.
                #
                # Тут же був четвертий екземпляр перевірки `"BLOCK" in
                # risk_flags` — той самий підрядок, що ловиться всередині
                # `..._BLOCKED`, тобто ордер із банкою відкидався незалежно
                # від налаштування.
                taker_side = SIDE_BUY if t_mode == "TAKER_BUY" else SIDE_SELL
                risk_resolver = (
                    await taker_scanner.db.resolver_for(t_user["user_id"])
                    if getattr(taker_scanner, "db", None) else None
                )

                for o in candidates:
                    risk_flags = getattr(o, "risk_flag", "") or ""
                    if risk_resolver is not None and decide(o, risk_resolver, taker_side).hide:
                        continue
                    if risk_flags_mod.has_block(risk_flags):
                        continue
                    fresh.append(o)

                # Маркуємо тільки те, що реально піде юзеру.
                for o in fresh:
                    taker_dedup.mark(f"taker:{t_user['user_id']}:{t_mode}:{o.id}")

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
        if "MAKER_SELL" in _user_modes(u)
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
        if "MAKER_BUY" in _user_modes(u)
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
