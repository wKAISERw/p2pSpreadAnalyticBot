# core/engine/taker_scanner.py
"""
TakerScanner — пайплайн для режимів TAKER_BUY / TAKER_SELL.

Замість пошуку зв'язок (buy+sell), фільтрує ОДНУ сторону ордерів
під персональні фільтри юзера (банки, ліміти, ціновий діапазон, мерчант-фільтри).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional
from exchanges.base import Order
from config.banks import bank_display_name, normalize_bank
from config.card_limits import FEATURE_IGNORE_MERCHANT_BANKS, FEATURE_INTER_BANK
from config.defaults import MIN_ORDERS, MIN_COMPLETION
from core.engine import rejection_codes as rc
from core.engine.bank_scope import resolve_banks
from core.engine.buy_budget import resolve_buy_budget
from core.engine.card_routing import resolve_route
from filters.anomaly_filter import AnomalyFilter
from core.engine import risk_flags as risk_flags_mod
from core.engine.risk_decision import decide
from core.risk.policy import SIDE_BUY, SIDE_SELL
from core.engine.personal_blacklist import in_personal_blacklist
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("TakerScanner")


@dataclass
class OrderRejection:
    """Ордер, який відсіяв картковий модуль, і чому саме."""
    order_id: str
    merchant_name: str
    exchange: str
    bank: str
    price: float
    min_limit: float
    code: str
    reason: str
    shortfall_uah: float = 0.0
    details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "merchant_name": self.merchant_name,
            "exchange": self.exchange,
            "bank": self.bank,
            "price": self.price,
            "min_limit": self.min_limit,
            "code": self.code,
            "reason": self.reason,
            "shortfall_uah": round(self.shortfall_uah, 2),
            "details": self.details,
        }


@dataclass
class TakerScanResult:
    orders: list[Order] = field(default_factory=list)
    rejections: list[OrderRejection] = field(default_factory=list)
    # Ордери, які пройшли, але не такими, як їх задумали: обсяг довелось
    # ужати. Тримаються окремо від відмов навмисно — інакше частка відмов
    # почала б суперечити сама собі («90% відмов» при тому, що всі ці ордери
    # людина отримала). Для статистики етапу 3 вони цінніші за самі відмови.
    observations: list[OrderRejection] = field(default_factory=list)

    @property
    def logged(self) -> list[OrderRejection]:
        """Усе, що варто записати в журнал причин."""
        return self.rejections + self.observations


def _reject(order: Order, bank: str, code: str, reason: str,
            shortfall: float = 0.0, details: list[dict] | None = None) -> OrderRejection:
    return OrderRejection(
        order_id=order.id,
        merchant_name=order.merchant_name,
        exchange=order.exchange,
        bank=bank,
        price=float(order.price),
        min_limit=float(order.min_limit),
        code=code,
        reason=reason,
        shortfall_uah=shortfall,
        details=details or [],
    )


def _reject_from(order: Order, bank: str, rejection: rc.Rejection) -> OrderRejection:
    """Відмова по картці, підвищена до відмови по ордеру."""
    return _reject(order, bank, rejection.code, rejection.reason,
                   rejection.shortfall_uah, [rejection.as_dict()])


def _reject_report(order: Order, bank: str, report: list[dict],
                   target_uah: float) -> OrderRejection:
    """
    Звіт движка (по одній причині на картку) → одна причина по ордеру.

    Показувати людині розклад по кожній картці в кожному повідомленні —
    це стіна тексту. Головну причину піднімаємо нагору, решта лишається в
    `details` для API й для статистики.
    """
    if not report:
        return _reject(order, bank, rc.NO_ACTIVE_CARDS, rc.label(rc.NO_ACTIVE_CARDS))

    with_numbers = [r for r in report if r.get("shortfall_uah")]
    head = max(with_numbers, key=lambda r: r["shortfall_uah"]) if with_numbers else report[0]
    return _reject(
        order, bank,
        head.get("code", ""), rc.summarize(report),
        float(head.get("shortfall_uah") or 0.0),
        report,
    )


class TakerScanner:
    """
    Фільтрує ордери для taker-only режимів.
    TAKER_BUY: sell-ордери (хто продає USDT) — ти купуєш.
    TAKER_SELL: buy-ордери (хто купує USDT) — ти продаєш.
    """

    def __init__(self, db: Optional[MerchantDB] = None):
        self.db = db
        if db:
            from core.engine.card_matching_engine import CardMatchingEngine
            self.card_engine = CardMatchingEngine(db)
        else:
            self.card_engine = None

    async def find_orders_for_user(
        self, user: dict,
        buy_grouped: dict[str, list[Order]],
        sell_grouped: dict[str, list[Order]],
    ) -> list[Order]:
        """Знаходить підходящі ордери для тейкер-юзера."""
        return (await self.scan(user, buy_grouped, sell_grouped)).orders

    async def scan(
        self, user: dict,
        buy_grouped: dict[str, list[Order]],
        sell_grouped: dict[str, list[Order]],
    ) -> TakerScanResult:
        """
        Те саме, що find_orders_for_user, але з причинами відмов.

        Причини існували й раніше — `CardMatchResult.rejection_report` —
        але лишались усередині движка. Через це ордер зникав зі стакана без
        сліду: ні в боті, ні в API, ні в логах не було видно, що саме не
        зійшлось. Тепер вони доїжджають назовні; що з ними робити далі —
        показувати, рахувати чи мовчати — вирішує викликач.
        """
        rejections: list[OrderRejection] = []
        observations: list[OrderRejection] = []
        mode = user.get("scanner_mode", "SPREAD")
        if mode not in ("TAKER_BUY", "TAKER_SELL"):
            return TakerScanResult()

        # buy_grouped  = мерчанти ПРОДАЮТЬ USDT (side=1, user BUYS, low price)
        # sell_grouped = мерчанти КУПУЮТЬ USDT  (side=0, user SELLS, high price)
        # Банки беремо через resolve_banks: якщо для цього режиму задане
        # перевизначення — воно, інакше спільний список. Доти майстер Taker
        # Buy писав свій вибір просто в buy_bank_codes, тобто змінював банки
        # купівлі й для спред-режиму.
        if mode == "TAKER_BUY":
            source_grouped = buy_grouped   # user купує → ордери де мерчанти продають
            user_banks = set(resolve_banks(user, mode, "buy"))
        else:
            source_grouped = sell_grouped  # user продає → ордери де мерчанти купують
            user_banks = set(resolve_banks(user, mode, "sell"))

        capital = float(user.get("capital", 0))
        min_amount = float(user.get("min_amount", 0))
        mf = user.get("merchant_filters") or {}
        emf = user.get("exchange_merchant_filters") or {}
        # Preload used subsidies for per-exchange filtering
        used_subs: dict[str, list[str]] = {}
        # Особистий чорний список: індекс тягнемо один раз на прохід, бо
        # перевірка нижче йде в циклі по всіх ордерах біржі.
        personal_bl: tuple[dict, dict] = ({}, {})
        risk_resolver = None
        # Напрямок угоди з точки зору КОРИСТУВАЧА: TAKER_BUY — купує,
        # TAKER_SELL — продає.
        taker_side = SIDE_BUY if mode == "TAKER_BUY" else SIDE_SELL
        if self.db:
            uid = user.get("user_id", 0)
            used_subs = await self.db.get_used_subsidies(uid) if uid else {}
            if uid:
                personal_bl = await self.db._user_blacklist_index(uid)
                risk_resolver = await self.db.resolver_for(uid)

        # ── Smart Card Pre-filtering Setup ──
        # Нормалізація банків живе в config/banks.py: та сама мапа лежала
        # скопійованою тут, в alert_dispatcher, card_repo і formatters.
        _normalize_bank = normalize_bank

        card_matching_active = False
        user_cards_by_bank = {}

        # Етап 3 плану — під експериментальними фічами (/features → КАРТКИ).
        # Вимкнені означають рівно поточну поведінку: один банк на угоду й
        # жорсткий фільтр банків мерчанта.
        inter_bank_on = False
        ignore_merchant_banks = False
        if self.db and user.get("user_id"):
            try:
                inter_bank_on = await self.db.get_feature_status(
                    user["user_id"], FEATURE_INTER_BANK)
                ignore_merchant_banks = await self.db.get_feature_status(
                    user["user_id"], FEATURE_IGNORE_MERCHANT_BANKS)
            except Exception as e:
                logger.debug("Не вдалось прочитати стан фіч карток: %s", e)

        if self.card_engine and self.db:
            try:
                card_settings = await self.db.get_user_card_settings(user["user_id"])
                if card_settings and card_settings.get("card_module_mode") != "off" and card_settings.get("enable_in_single_modes"):
                    card_matching_active = True
                    raw_cards = await self.db.get_cards(user["user_id"], status="active")
                    import time
                    now_epoch = time.time()
                    for c in raw_cards:
                        if float(c.get("cooldown_until", 0.0)) > now_epoch:
                            continue
                        b_name = _normalize_bank(c.get("bank_name", ""))
                        if b_name not in user_cards_by_bank:
                            user_cards_by_bank[b_name] = []
                        user_cards_by_bank[b_name].append(c)
            except Exception as e:
                logger.warning("Error initializing card pre-filtering: %s", e)

        seen_ids: set[str] = set()
        candidates: list[Order] = []

        async def _route_banks(order: Order, primary: str) -> tuple[list[str], list[str]]:
            """
            (банки маршруту, банки заявлені мерчантом).

            Логіка спільна з будівником алерта — див. `card_routing.resolve_route`.
            Тут лише підставляємо вже прочитані банки карток, щоб не ходити
            в базу вдруге на кожен ордер.
            """
            route = await resolve_route(
                self.db, user.get("user_id", 0), order.bank_codes,
                primary_bank=primary, card_banks=set(user_cards_by_bank),
            )
            return route.banks or [primary], route.declared

        # Обсяг, змасштабований під баланс карток, — по кожному ордеру окремо.
        #
        # Раніше цю роль грав запис у БД: масштабування клало нову суму в
        # scanner_users, і фінальний прохід нижче читав уже її. Тепер бажана
        # сума не змінюється, тож масштаб треба донести сюди явно — інакше
        # фінальний матчинг перевіряв би картки під повні 700 USDT і відкидав
        # ордер, який щойно визнав придатним.
        scaled_usdt_by_order: dict[str, float] = {}

        # Цінові аномалії відсіюємо до всіх інших фільтрів.
        #
        # Односторонньо: у TAKER_BUY підозріла надто НИЗЬКА ціна продавця,
        # у TAKER_SELL — надто ВИСОКА ціна покупця. Протилежний бік не
        # чіпаємо: там «аномалія» означає просто невигідну ціну, і її й так
        # відкинуть звичайні пороги.
        #
        # Рахуємо по кожному банку окремо: ринок Monobank і ринок ПУМБ — це
        # різні стакани з різними цінами, і спільна медіана по них показала б
        # ринок, якого не існує.
        anomaly_side = "buy" if mode == "TAKER_BUY" else "sell"
        anomaly_filter = AnomalyFilter()
        clean_grouped: dict[str, list[Order]] = {}
        for bank_code, bank_orders in source_grouped.items():
            if bank_code not in user_banks:
                continue
            result = anomaly_filter.analyze(bank_orders, anomaly_side)
            clean_grouped[bank_code] = result.kept
            for item in result.rejected:
                logger.debug(
                    "🎯 %s [%s] відсіяно як аномалію: %s",
                    item.order.merchant_name, bank_code, item.reason,
                )
        source_grouped = clean_grouped

        for bank_code, orders in source_grouped.items():
            if bank_code not in user_banks:
                continue
            for order in orders:
                if order.id in seen_ids:
                    continue
                if not (set(order.bank_codes) & user_banks):
                    continue
                order_max = float(order.max_limit)
                order_min = float(order.min_limit)
                if capital > 0 and order_min > capital:
                    continue
                if min_amount > 0 and order_max < min_amount:
                    continue

                order_price = float(order.price)

                order_price = float(order.price)

                # ── Фільтри TAKER_SELL (використовуємо обчислений min_sell_price) ──
                if mode == "TAKER_SELL":
                    strategy = user.get("taker_sell_price_strategy", "roi")
                    min_sell_price = float(user.get("taker_sell_min_price", 0))
                    price_to = float(user.get("taker_sell_price_to", 0))
                    t_amount = float(user.get("taker_sell_amount", 0))
                    t_speed = user.get("taker_sell_speed", "ANY")

                    # ── Цінова стратегія ──────────────────────────────────
                    if strategy in ("roi", "min") and min_sell_price > 0:
                        if order_price < min_sell_price:
                            continue
                    elif strategy == "range":
                        if min_sell_price > 0 and order_price < min_sell_price:
                            continue
                        if price_to > 0 and order_price > price_to:
                            continue
                    elif strategy == "exact" and min_sell_price > 0:
                        if abs(order_price - min_sell_price) > 0.005:
                            continue
                    # "any" → без фільтру

                    # ── Ob'єм ─────────────────────────────────────────────
                    if t_amount > 0:
                        fiat_val = t_amount * order_price
                        if t_speed == "FAST":
                            if order_max < fiat_val or order_min > fiat_val:
                                continue
                        else:
                            if order_min > fiat_val:
                                continue

                # ── Фільтри TAKER_BUY ────────────────────────────────────────────────
                elif mode == "TAKER_BUY":
                    strategy = user.get("taker_buy_price_strategy", "any")
                    price_to = float(user.get("taker_buy_max_price", 0))
                    price_from = float(user.get("taker_buy_price_from", 0))
                    t_limit_min = float(user.get("taker_buy_limit_min", 0))
                    t_limit_max = float(user.get("taker_buy_limit_max", 0))
                    t_speed = user.get("taker_buy_speed", "ANY")

                    # ── Цінова стратегія ──────────────────────────────────
                    if strategy == "max" and price_to > 0:
                        if order_price > price_to:
                            continue
                    elif strategy == "range":
                        if price_from > 0 and order_price < price_from:
                            continue
                        if price_to > 0 and order_price > price_to:
                            continue
                    elif strategy == "exact" and price_to > 0:
                        if abs(order_price - price_to) > 0.005:  # допуск 0.5 копійки
                            continue
                    # "any" → без фільтру

                    # ── Фіатні ліміти ──────────────────────────────────────
                    if t_limit_min > 0 and order_max < t_limit_min:
                        continue
                    if t_limit_max > 0 and order_min > t_limit_max:
                        continue

                    # ── Об'єм ─────────────────────────────────────────────
                    t_amount = float(user.get("taker_buy_amount", 0))
                    if t_amount > 0:
                        fiat_needed = t_amount * order_price
                        if t_speed == "FAST":
                            if order_max < fiat_needed or order_min > fiat_needed:
                                continue
                        else:
                            if order_min > fiat_needed:
                                continue

                # ── In-Memory Card Pre-filtering (Тільки для ордерів, що відповідають ціновій стратегії) ──
                if card_matching_active:
                    from bot.formatters import _bank_code_to_db
                    card_bank_db = _normalize_bank(_bank_code_to_db(bank_code))

                    # Без картки банку мерчанта ордер відпадав завжди. З
                    # увімкненим «ігнорувати фільтр банків» — ні: беремо
                    # будь-яку свою картку, а домовлятись піде людина.
                    if not card_bank_db:
                        continue
                    if card_bank_db not in user_cards_by_bank:
                        if not (ignore_merchant_banks and user_cards_by_bank):
                            rejections.append(_reject(
                                order, card_bank_db or bank_code,
                                rc.NO_CARDS_FOR_BANK,
                                f"Мерчант приймає лише {bank_display_name(bank_code)}, "
                                f"а активної картки цього банку немає",
                            ))
                            continue
                        # Своєї картки цього банку немає — рахуємо маршрут
                        # від найбільшого з наявних.
                        card_bank_db = max(
                            user_cards_by_bank,
                            key=lambda b: sum(
                                float(c.get("balance", 0.0)) for c in user_cards_by_bank[b]
                            ),
                        )

                    route_banks, _declared = await _route_banks(order, card_bank_db)


                    # Calculate target UAH amount for card limit checks
                    target_uah = order_min
                    if mode == "TAKER_SELL":
                        t_amount = float(user.get("taker_sell_amount", 0))
                        if t_amount > 0:
                            target_uah = t_amount * order_price
                    elif mode == "TAKER_BUY":
                        t_amount = float(user.get("taker_buy_amount", 0))
                        if t_amount > 0:
                            target_uah = t_amount * order_price

                    # Більше за стелю мерчанта в цю угоду все одно не піде.
                    # Досі під цю перевірку не потрапляло нічого: користувач
                    # із обсягом 700 USDT отримував алерт на ордер, де
                    # максимум 7 000 ₴, і картки перевірялись під 31 000 ₴,
                    # яких мерчант не візьме.
                    order_max = float(order.max_limit)
                    if order_max > 0 and target_uah > order_max:
                        target_uah = order_max
                        if order_price > 0:
                            t_amount = round(target_uah / order_price, 2)

                    # For TAKER_BUY we check total UAH balance based on buy_balance_mode
                    if mode == "TAKER_BUY":
                        buy_bal_mode = user.get("buy_balance_mode", "CARD_ENFORCED")
                        if buy_bal_mode == "MANUAL_STRICT":
                            pass  # Ігноруємо перевірку балансів карт, шукаємо суто під вказаний параметр
                        else:
                            # Баланс рахуємо по ВСІХ банках маршруту. Доти це
                            # був один банк, і саме тому 31 000 ₴ на трьох
                            # картках перетворювались на 21 000 ₴: решта
                            # грошей існувала, але движок про них не питав.
                            total_bal = 0.0
                            for route_bank in route_banks:
                                for c in user_cards_by_bank.get(route_bank, []):
                                    card_id = c["id"]
                                    pending_out = 0.0
                                    async with self.db._db.execute(
                                        """
                                        SELECT SUM(l.amount) as total
                                        FROM card_order_legs l
                                        JOIN card_orders o ON l.order_id = o.id
                                        WHERE l.card_id = ? AND l.leg_status = 'pending' AND o.direction = 'out'
                                        """,
                                        (card_id,)
                                    ) as cur:
                                        row = await cur.fetchone()
                                        if row and row["total"]:
                                            pending_out = float(row["total"])
                                    total_bal += max(0.0, float(c.get("balance", 0.0)) - pending_out)

                            if total_bal < target_uah:
                                scale_down = (
                                    buy_bal_mode == "AUTO_SCALE"
                                    and bool(int(user.get("buy_auto_scale_down", 1)))
                                )
                                if t_amount <= 0:
                                    # Обсяг у USDT не заданий — тоді target_uah
                                    # це просто мінімалка ордера, і масштабувати
                                    # нема чого. Поведінка як була: у режимі
                                    # авто-масштабування ордер лишаємо, інакше
                                    # попереджаємо й пропускаємо.
                                    if not scale_down and not (
                                        target_uah - total_bal <= 50.0
                                        or total_bal >= target_uah * 0.95
                                    ):
                                        await notify_insufficient_buy_balance(
                                            user, total_bal, target_uah, t_amount,
                                            order_price, bank=card_bank_db)
                                        rejections.append(_reject_from(
                                            order, card_bank_db,
                                            rc.insufficient_balance(target_uah, total_bal,
                                                                    bank=card_bank_db)))
                                        continue
                                    budget = None
                                else:
                                    budget = resolve_buy_budget(
                                        t_amount, total_bal, order_price,
                                        allow_scale_down=scale_down,
                                    )

                                if budget is not None and budget.blocked:
                                    if scale_down:
                                        # Навіть мінімалки не набирається — тихо
                                        # пропускаємо ордер, як і раніше. Мовчки
                                        # для користувача, але в статистику це
                                        # тепер потрапляє.
                                        rejections.append(_reject(
                                            order, card_bank_db, rc.BELOW_MIN_TRADE,
                                            f"На картках {total_bal:,.0f} ₴ — замало навіть "
                                            f"на мінімальну угоду".replace(",", " "),
                                        ))
                                        continue
                                    await notify_insufficient_buy_balance(
                                        user, total_bal, target_uah, t_amount,
                                        order_price, bank=card_bank_db)
                                    rejections.append(_reject_from(
                                        order, card_bank_db,
                                        rc.insufficient_balance(target_uah, total_bal,
                                                                bank=card_bank_db)))
                                    continue

                                if budget is not None and budget.scaled:
                                    # Ужата сума мусить лишитись у вікні
                                    # мерчанта. Досі цього ніхто не перевіряв:
                                    # ордер «від 30 000 ₴» проходив із
                                    # масштабом до 21 298 ₴ — тобто алерт
                                    # приходив на угоду, яку мерчант не
                                    # прийме за жодних обставин.
                                    if budget.effective_uah < order_min - 0.01:
                                        rejections.append(_reject_from(
                                            order, card_bank_db,
                                            rc.below_merchant_min(
                                                budget.effective_uah, order_min,
                                                bank=card_bank_db)))
                                        continue

                                    # Бажана сума лишається в БД недоторканою.
                                    # Масштаб діє рівно на цей ордер — тому й
                                    # запам'ятовуємо його при ордері, а не в
                                    # налаштуваннях користувача.
                                    await notify_buy_autoscale(
                                        user, total_bal, budget.desired_usdt,
                                        budget.effective_usdt, order_price, is_down=True)
                                    t_amount = budget.effective_usdt
                                    target_uah = budget.effective_uah
                                    scaled_usdt_by_order[order.id] = budget.effective_usdt

                                    # Ордер піде далі, але меншим. Записуємо це
                                    # як спостереження: саме тут стеля одного
                                    # банку коштує нам обсягу, і саме це має
                                    # вирішити, чи потрібен етап 3. Досі така
                                    # угода не лишала в статистиці нічого.
                                    observations.append(_reject_from(
                                        order, card_bank_db,
                                        rc.volume_scaled_down(
                                            budget.desired_uah, budget.effective_uah,
                                            bank=card_bank_db,
                                        )))

                if not self._merchant_ok(order, mf, emf, used_subs):
                    continue

                # Особистий бан користувача — беззастережний, на відміну від
                # спільного списку нижче: там blacklist_mode ще дає вибір
                # «блокувати / ховати / показувати з позначкою».
                if in_personal_blacklist(order, *personal_bl):
                    continue

                # Check blacklist setting: if "blacklist_mode" is "warn", we allow BLOCK:BLACKLIST to pass but keep the flag for warning presentation
                # Персональна політика. Напрямок у тейкері один на весь
                # прохід і береться з РЕЖИМУ, а не з ордера: у стакані
                # `order.side` означає бік мерчанта, і сплутати їх означало
                # б перевернути асиметрію догори дриґом.
                if risk_resolver is not None and decide(order, risk_resolver, taker_side).hide:
                    continue

                risk_flag = getattr(order, "risk_flag", "") or ""
                if risk_flags_mod.is_blacklist_block(risk_flag):
                    bl_mode = mf.get("blacklist_mode", "block").lower()
                    if bl_mode == "block":
                        continue
                elif risk_flags_mod.has_block(risk_flag):
                    # other non-blacklist BLOCK flags (like CACHED blocks) are always skipped.
                    # Розбір списком, а не підрядком: "BLOCK" міститься
                    # всередині FOP_TOV_BLOCKED і BANKA_JAR_BLOCKED, які є
                    # метаданими для персональних фільтрів, а не блоками.
                    continue
                
                seen_ids.add(order.id)
                candidates.append(order)

        # ── Final Card Matching & Filtering on candidates only (N+1 avoidance) ──
        matched: list[Order] = []
        for order in candidates:
            if card_matching_active:
                order_bank_code = order.bank_codes[0] if order.bank_codes else ""
                from bot.formatters import _bank_code_to_db
                card_bank_db = _bank_code_to_db(order_bank_code)
                
                target_uah = float(order.min_limit)
                order_price = float(order.price)
                if mode == "TAKER_SELL":
                    t_amount = float(user.get("taker_sell_amount", 0))
                    if t_amount > 0:
                        target_uah = t_amount * order_price
                elif mode == "TAKER_BUY":
                    t_amount = scaled_usdt_by_order.get(
                        order.id, float(user.get("taker_buy_amount", 0))
                    )
                    if t_amount > 0:
                        target_uah = t_amount * order_price

                # Той самий затиск, що й у пре-фільтрі. Без нього фінальний
                # прохід перевіряв би картки під суму, якої мерчант не
                # візьме, і сам же відкидав ордер, який щойно визнав
                # придатним.
                order_max = float(order.max_limit)
                if order_max > 0 and target_uah > order_max:
                    target_uah = order_max

                card_direction = "buy" if mode == "TAKER_BUY" else "sell"

                if (card_bank_db not in user_cards_by_bank
                        and ignore_merchant_banks and user_cards_by_bank):
                    card_bank_db = max(
                        user_cards_by_bank,
                        key=lambda b: sum(
                            float(c.get("balance", 0.0)) for c in user_cards_by_bank[b]
                        ),
                    )
                route_banks, declared_banks = await _route_banks(order, card_bank_db)

                match_res = await self.card_engine.run(
                    user_id=user["user_id"],
                    bank=card_bank_db,
                    amount=target_uah,
                    direction=card_direction,
                    crypto_available=True,
                    trade_terms=getattr(order, "trade_terms", "") or "",
                    banks=route_banks,
                    declared_banks=declared_banks,
                )
                if match_res.status not in ("success", "needs_split"):
                    # 🚀 ТАЙТ-ТОЛЕРАНТНІСТЬ: Якщо суми на картці не вистачає всього на трохи (< 3% або < 50 ₴), підганяємо під доступний баланс
                    if mode == "TAKER_BUY" and target_uah > 0:
                        card_bal = float(match_res.available_uah or 0.0)
                        if card_bal > 0 and (target_uah - card_bal <= 50.0 or card_bal >= target_uah * 0.95):
                            fit_res = await self.card_engine.run(
                                user_id=user["user_id"],
                                bank=card_bank_db,
                                amount=card_bal,
                                direction=card_direction,
                                crypto_available=True,
                                trade_terms=getattr(order, "trade_terms", "") or "",
                                banks=route_banks,
                                declared_banks=declared_banks,
                            )
                            if fit_res.status in ("success", "needs_split"):
                                matched.append(order)
                                continue

                    # Досі ордер тут просто зникав. Тепер причина їде далі —
                    # вона ж і є вхідними даними для етапу 3 плану.
                    rejections.append(_reject_report(
                        order, card_bank_db, match_res.rejection_report, target_uah,
                    ))
                    continue
            matched.append(order)

        # BUY → найнижча ціна спершу, SELL → найвища
        if mode == "TAKER_BUY":
            matched.sort(key=lambda o: float(o.price))
        else:
            matched.sort(key=lambda o: float(o.price), reverse=True)
        kept = matched[:20]
        # Спостереження лишаємо тільки по ордерах, які реально дійшли до
        # користувача: масштабування ордера, що потім відсіявся з іншої
        # причини, не є фактом «стеля коштувала нам обсягу».
        kept_ids = {o.id for o in kept}
        return TakerScanResult(
            orders=kept,
            rejections=rejections,
            observations=[o for o in observations if o.order_id in kept_ids],
        )

    @staticmethod
    def _merchant_ok(order: Order, mf: dict, emf: dict, used_subs: dict[str, list[str]] | None = None) -> bool:
        """Перевіряє фільтри мерчанта (global + per-exchange)."""
        ex_name = order.exchange
        ex_filters = emf.get(ex_name, {})
        min_orders = float(
            ex_filters.get("min_orders", 0) or mf.get("min_orders", 0)
            or MIN_ORDERS.get(ex_name, 0)
        )
        min_rate = float(
            ex_filters.get("min_rate", 0.0) or mf.get("min_rate", 0.0)
            or MIN_COMPLETION.get(ex_name, 0.0)
        )
        # Per-exchange subsidy filter: hide only used subsidies
        if getattr(order, "is_new_user_subsidy", False) and used_subs:
            ex_used = used_subs.get(ex_name, [])
            if "new_user" in ex_used:
                return False

        if min_orders > 0 and order.month_order_count < min_orders:
            return False
        if min_rate > 0 and order.finish_rate_pct < min_rate:
            return False

        # --- Reliability Filters ---
        # 1. Verification status ("all", "verified", "unverified")
        verified_filter = ex_filters.get("verified_filter") or mf.get("verified_filter") or "all"
        if verified_filter == "verified" and not order.is_verified:
            return False
        if verified_filter == "unverified" and order.is_verified:
            return False

        # 2. Account age (days) - only filter if exchange provides positive age > 0
        min_age = int(ex_filters.get("min_account_age_days") or mf.get("min_account_age_days") or 0)
        if min_age > 0:
            age = getattr(order, "account_age_days", 0)
            if age > 0 and age < min_age:
                return False

        # 3. Positive review rate (%)
        min_pos_rate = float(ex_filters.get("min_positive_rate") or mf.get("min_positive_rate") or 0.0)
        if min_pos_rate > 0.0:
            pos_rate = getattr(order, "positive_rate", 0.0)
            if pos_rate > 0.0:
                actual_pct = pos_rate * 100.0 if pos_rate <= 1.0 else pos_rate
                if actual_pct < min_pos_rate:
                    return False

        # 4. Max offline minutes
        max_offline = int(ex_filters.get("max_offline_mins") or mf.get("max_offline_mins") or 0)
        if max_offline > 0:
            last_online = getattr(order, "last_online_mins", None)
            if last_online is not None and last_online > max_offline:
                return False

        return True


_last_insufficient_warn = {}

# Тротлінг повідомлень про масштабування.
#
# Раніше алерт приходив рідко як побічний ефект дефекту: масштабування
# записувало нову суму в БД, і наступного проходу масштабувати вже не було
# чого. Тепер бажана сума не змінюється — тобто ситуація повторюється на
# кожному ордері кожного циклу, і тротлінг обов'язковий.
#
# Ключ описує СИТУАЦІЮ, а не результат обчислення. Перша версія включала
# `effective_usdt`, і це не працювало: ефективна сума = баланс / ціна
# ОРДЕРА, тож вона різна для кожного ордера в стакані й пливе разом із
# курсом. На практиці 21 297.80 ₴ давали 480.98 USDT о 8:57 і 481.20 о
# 10:02 — нічого не змінилось, крім двох копійок курсу, а повідомлення
# приходило як про нову подію.
#
# Це той самий висновок, до якого вже прийшов _last_insufficient_warn:
# тротлити треба за тим, що людина сприймає як зміну, а баланс вона бачить
# у гривнях, не в USDT. Тому баланс округлюємо до сотень — дрібний рух по
# картці не є новиною.
_last_autoscale_notice: dict[tuple, float] = {}
_AUTOSCALE_NOTICE_TTL = 6 * 3600


async def notify_buy_autoscale(user: dict, total_bal: float, desired_usdt: float,
                               effective_usdt: float, est_rate: float, is_down: bool):
    from bot.handlers.core import _bot
    import time
    user_id = user.get("user_id")
    if not user_id or not _bot:
        return

    key = (user_id, is_down, round(desired_usdt, 2), round(total_bal, -2))
    now = time.time()
    if now - _last_autoscale_notice.get(key, 0.0) < _AUTOSCALE_NOTICE_TTL:
        return
    _last_autoscale_notice[key] = now

    desired_uah = desired_usdt * est_rate
    effective_uah = effective_usdt * est_rate

    if is_down:
        text = (
            f"📉 <b>Балансу не вистачає на повний обсяг — шукаю під наявний</b>\n\n"
            f"💳 Доступно на картках: <b>{total_bal:,.2f} ₴</b>\n"
            f"🎯 Ваш обсяг: <b>{desired_usdt:,.2f} USDT</b> (~{desired_uah:,.0f} ₴) — <i>збережено</i>\n"
            f"🔎 Шукаю зараз під: <b>{effective_usdt:,.2f} USDT</b> (~{effective_uah:,.0f} ₴)\n"
            f"<i>(курсом ~{est_rate:.2f} ₴/USDT)</i>\n\n"
            f"<i>Налаштування не змінено. Поповните картку — знову шукатиму "
            f"повні {desired_usdt:,.2f} USDT.</i>"
        )
    else:
        text = (
            f"📈 <b>Баланс відновлено — повертаюсь до вашого обсягу</b>\n\n"
            f"💳 Доступно на картках: <b>{total_bal:,.2f} ₴</b>\n"
            f"🎯 Шукаю під: <b>{effective_usdt:,.2f} USDT</b> (~{effective_uah:,.0f} ₴)"
        )

    try:
        await _bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send autoscale notification to {user_id}: {e}")


async def notify_insufficient_buy_balance(user: dict, total_bal: float, target_uah: float,
                                          t_amount: float, est_rate: float,
                                          bank: str = ""):
    from bot.handlers.core import _bot
    import time
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    user_id = user.get("user_id")
    if not user_id or not _bot:
        return
    now = time.time()

    # Тротлінг за СИТУАЦІЄЮ, а не просто за часом.
    #
    # Раніше ключем був лише user_id з інтервалом 15 хв — і те саме
    # попередження приходило кожні 15 хвилин, поки ордер висів. Користувач
    # усе зрозумів з першого разу, решта — спам.
    #
    # Тепер ключ включає банк і потрібну суму: нове повідомлення приходить,
    # якщо змінився банк або сума (тобто ситуація справді інша). Та сама
    # ситуація повторюється не частіше разу на 6 годин.
    sit_key = (user_id, bank or "?", round(t_amount, 2))
    if now - _last_insufficient_warn.get(sit_key, 0) < 6 * 3600:
        return
    _last_insufficient_warn[sit_key] = now

    needed_uah = t_amount * est_rate if t_amount > 0 else target_uah
    calc_usdt = round(total_bal / est_rate, 2) if est_rate > 0 else 0.0

    kb_buttons = [
        [InlineKeyboardButton(text="⚡ Увімкнути авто-масштабування", callback_data="tbuy_scale_on")]
    ]
    if calc_usdt >= 5.0:
        kb_buttons.append([InlineKeyboardButton(text=f"✏️ Встановити {calc_usdt:.2f} USDT під баланс", callback_data=f"tbuy_fit_bal:{calc_usdt}")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    # Банк обов'язково називаємо. Перевірка рахує лише картки того банку, який
    # приймає мерчант, тому число тут МЕНШЕ за загальний капітал у /start —
    # без назви банку це виглядає як помилка бота.
    bank_line = (f"💳 Доступно на картках <b>{bank}</b>: <b>{total_bal:,.2f} ₴</b>\n"
                 if bank else
                 f"💳 Доступно на картках цього банку: <b>{total_bal:,.2f} ₴</b>\n")

    text = (
        f"⚠️ <b>Недостатньо коштів для купівлі</b>\n\n"
        f"{bank_line}"
        f"💸 Необхідно: <b>{t_amount:,.2f} USDT (~{needed_uah:,.0f} ₴)</b>\n\n"
        f"<i>Мерчант приймає оплату лише цим банком, тому решта карток "
        f"не враховується.</i>\n\n"
        f"<i>💡 Кнопка нижче підлаштує суму під баланс або увімкне "
        f"авто-масштабування:</i>"
    )
    try:
        await _bot.send_message(chat_id=user_id, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Failed to send insufficient balance warning to {user_id}: {e}")


# Остання порахована ефективна сума по користувачу — щоб не повідомляти те
# саме на кожен вебхук. Живе в процесі: після рестарту людина в найгіршому
# випадку отримає одне зайве повідомлення про поточний стан.
_last_effective_usdt: dict[int, float] = {}


async def trigger_buy_autoscale_check(db, user_id: int):
    """
    Перерахунок «під що шукаємо» після зміни балансу картки.

    Нічого не записує. Бажаний обсяг користувача — незмінний вхід; тут лише
    рахується, скільки з нього виходить за поточних балансів, і людина
    дізнається, коли цифра змінилась.
    """
    if not db:
        return
    user = await db.get_user_by_id(user_id)
    if not user:
        return

    from core.engine.scanner_helpers import _user_modes

    if "TAKER_BUY" not in _user_modes(user):
        return
    if user.get("buy_balance_mode") != "AUTO_SCALE":
        return

    # Тут стояло `db.get_user_cards(user_id)` — методу з такою назвою в
    # проєкті немає взагалі, тож виклик щоразу падав на AttributeError.
    # Усі три викликачі (вебхук Monobank, меню тейкера, воркер синхронізації
    # балансів) ловлять виняток у except з logger.debug, тому масштабування
    # «по факту надходження» не працювало жодного разу — і мовчки.
    #
    # Рахуємо тим самим методом, що й цикл сканера: максимум по ОДНОМУ банку,
    # з урахуванням лімітів, прогріву й зарезервованих сум. Сума по всіх
    # банках тут була б обманом — угода йде з карток одного банку.
    buy_banks = resolve_banks(user, "TAKER_BUY", "buy") or None
    total_bal = await db.get_user_auto_capital(user_id, allowed_banks=buy_banks)
    if total_bal <= 0:
        return

    desired_usdt = float(user.get("taker_buy_amount", 0.0))
    if desired_usdt <= 0:
        return

    est_rate = float(user.get("taker_buy_max_price", 0.0)) or 40.0
    auto_down = bool(int(user.get("buy_auto_scale_down", 1)))
    auto_up = bool(int(user.get("buy_auto_scale_up", 1)))

    budget = resolve_buy_budget(
        desired_usdt, total_bal, est_rate, allow_scale_down=auto_down
    )
    if budget.blocked:
        return

    # Порівнюємо з тим, що рахували минулого разу, а не з бажаною сумою:
    # інакше кожен вебхук Monobank слав би те саме повідомлення.
    prev = _last_effective_usdt.get(user_id, desired_usdt)
    _last_effective_usdt[user_id] = budget.effective_usdt

    if budget.effective_usdt < prev - 0.01 and auto_down:
        await notify_buy_autoscale(user, total_bal, desired_usdt,
                                   budget.effective_usdt, est_rate, is_down=True)
    elif budget.effective_usdt > prev + 0.01 and auto_up:
        await notify_buy_autoscale(user, total_bal, desired_usdt,
                                   budget.effective_usdt, est_rate, is_down=False)