# core/engine/alert_dispatcher.py
import copy
import logging
import time

from bot.notifier import TelegramNotifier, SpreadAlert
from config.banks import normalize_bank
from core.engine.bank_scope import resolve_banks
from core.engine.personal_blacklist import in_personal_blacklist
from core.storage.merchant_db import MerchantDB
from core.utils.tasks import spawn

logger = logging.getLogger("Scanner.AlertDispatcher")


def network_penalty_uah(user: dict, opp: dict) -> float:
    """
    Наскільки дорожчий переказ мережею, якою людина возить насправді.

    Матчер один на всіх, тож рахує найдешевшою спільною. Але TRC20 коштує
    1 ₮ проти 0.01 у TON — і хто возить лише ним, отримував алерти, які в
    його реальності порога не проходять. Різниця відома точно, тож це
    арифметика, а не оцінка.

    0.0 — переваги немає, або вона й так найдешевша, або цієї мережі між
    цими біржами не існує. Останнє важливо: підставляти чужу ціну тому,
    хто тут своєю мережею не проїде, було б гірше за мовчання.
    """
    preferred = (user.get("preferred_network") or "").upper()
    if not preferred:
        return 0.0

    network = opp.get("network") or {}
    base_name = str(network.get("name") or "").upper()
    if not base_name or base_name in ("INTRA", "UNKNOWN") or base_name == preferred:
        return 0.0

    try:
        from core.engine.network_fee_engine import NetworkFeeEngine

        buy_o, sell_o = opp["buy_order"], opp["sell_order"]
        options = dict(
            NetworkFeeEngine.get_all_options(buy_o.exchange, sell_o.exchange)
        )
        if preferred not in options:
            return 0.0

        delta_usdt = float(options[preferred]) - float(network.get("fee_usdt") or 0.0)
        if delta_usdt <= 0:
            return 0.0
        return delta_usdt * float(buy_o.price)
    except Exception as e:
        logger.debug("network penalty: %s", e)
        return 0.0


def personal_net_spread(user: dict, opp: dict) -> float:
    """Чистий спред у перерахунку на мережу, якою возить саме цей юзер."""
    net_spread = float(opp["net_spread_pct"])
    penalty = network_penalty_uah(user, opp)
    if penalty <= 0:
        return net_spread

    entry = float(opp.get("actual_entry_uah") or 0.0)
    if entry <= 0:
        return net_spread
    return net_spread - (penalty / entry) * 100.0


class AlertDispatcher:
    """
    Відповідає за персоналізовану розсилку алертів.
    Автоматично коригує банк угоди під наявні АКТИВНІ картки користувача
    на основі повного перетину підтримуваних мерчантом банків.
    """

    def __init__(self, db: MerchantDB, notifier: TelegramNotifier, cache_ttl: float = 5.0):
        self._db = db
        self._notifier = notifier
        self._cache_ttl = cache_ttl
        self._users_cache: list[dict] = []
        self._cache_loaded_at: float = 0.0

    async def _get_users(self) -> list[dict]:
        """Повертає active_users з кешем TTL=5s."""
        if time.monotonic() - self._cache_loaded_at >= self._cache_ttl:
            self._users_cache = await self._db.get_active_users()
            self._cache_loaded_at = time.monotonic()
        return self._users_cache

    @staticmethod
    def _clean_and_normalize_banks(banks_input) -> set[str]:
        """
        Витягує назви банків із будь-якого брудного значення з SQLite.

        Мапа синонімів переїхала в config/banks.py: раніше вона лежала
        скопійованою тут, у taker_scanner, card_repo і formatters, тож
        додати банк означало не забути про решту трьох файлів.
        """
        if not banks_input:
            return set()

        import re

        # Значення приходять і списком, і CSV, і навіть як repr списку —
        # тому виколупуємо всі слова, а не робимо split(",").
        tokens = re.findall(r'[a-zA-Z0-9а-яА-ЯіІёЁєЄїЇґҐ-]+', str(banks_input))
        return {normalize_bank(token) for token in tokens if token}

    @staticmethod
    def _select_best_bank_from_owned(allowed_banks: set[str], owned_cards: list[dict]) -> str | None:
        """
        Знаходить найкращий банк серед усіх карт користувача, які дозволені фільтрами.
        Сортує за пріоритетом статусу: active > cooldown > inactive > frozen_funds > frozen > blocked.
        """
        if not allowed_banks or not owned_cards:
            return None
        candidates = {}
        status_scores = {
            "active": 10,
            "cooldown": 6,
            "inactive": 5,
            "frozen_funds": 2,
            "frozen": 1,
            "blocked": 0
        }
        for card in owned_cards:
            b_name = str(card.get("bank_name", "")).lower()
            if b_name in allowed_banks:
                status = str(card.get("status", "")).lower()
                score = status_scores.get(status, 1)
                candidates[b_name] = max(candidates.get(b_name, -1), score)
        if not candidates:
            return None
        # Сортуємо: спочатку більший скор статусу (-x[1]), потім за алфавітом (x[0])
        sorted_candidates = sorted(candidates.items(), key=lambda x: (-x[1], x[0]))
        return sorted_candidates[0][0]

    @classmethod
    async def adapt_alert_for_user(
        cls, db, user: dict, alert: SpreadAlert, cards: tuple[list, list] | None = None,
    ) -> tuple[str, str]:
        """
        Коригує банки алерта під наявні картки користувача.

        :param cards: (активні_картки, усі_картки) — якщо передані, БД не
            смикається. Це важливо для батчевої розсилки: раніше тут летіли
            два get_cards на КОЖЕН алерт кожного юзера, хоча набір карток за
            цикл не змінюється.
        """
        uid = user.get("user_id")
        if cards is not None:
            user_cards, user_all_cards = cards
            user_card_names = {str(c["bank_name"]).lower() for c in user_cards}
        else:
            try:
                user_cards = await db.get_cards(owner_id=uid, status="active")
                user_card_names = {str(c["bank_name"]).lower() for c in user_cards}
                user_all_cards = await db.get_cards(owner_id=uid)
            except Exception as e:
                logger.warning("Помилка отримання карток користувача %s: %s", uid, e)
                user_card_names = set()
                user_all_cards = []

        # Очищуємо та розгортаємо глобальні списки доступних карт
        user_buy_names = cls._clean_and_normalize_banks(resolve_banks(user, "SPREAD", "buy"))
        user_sell_names = cls._clean_and_normalize_banks(resolve_banks(user, "SPREAD", "sell"))
        opp_buy_names = cls._clean_and_normalize_banks(getattr(alert, "buy_banks_fit", []))
        opp_sell_names = cls._clean_and_normalize_banks(getattr(alert, "sell_banks_fit", []))

        allowed_buy_names = opp_buy_names & user_buy_names
        allowed_sell_names = opp_sell_names & user_sell_names

        # Шукаємо перетин: які з дозволених фільтрами банків мерчанта у нас РЕАЛЬНО є в гаманці
        cards_buy_match = allowed_buy_names & user_card_names
        cards_sell_match = allowed_sell_names & user_card_names

        # 🧠 АДАПТИВНИЙ ПРІОРИТЕТ:
        engine_buy_list = list(cls._clean_and_normalize_banks(getattr(alert, "buy_bank", "43")))
        engine_sell_list = list(cls._clean_and_normalize_banks(getattr(alert, "sell_bank", "43")))
        engine_buy_name = engine_buy_list[0] if engine_buy_list else "monobank"
        engine_sell_name = engine_sell_list[0] if engine_sell_list else "monobank"

        NAME_TO_CODE = {"monobank": "43", "privatbank": "14", "pumb": "64", "a-bank": "48", "izibank": "553", "sense": "328"}

        # Отримаємо дозволені коди для Buy та Sell
        allowed_buy_codes = {NAME_TO_CODE[name] for name in allowed_buy_names if name in NAME_TO_CODE}
        allowed_sell_codes = {NAME_TO_CODE[name] for name in allowed_sell_names if name in NAME_TO_CODE}

        # Отримуємо коди живих карток користувача
        user_card_codes = {NAME_TO_CODE[name] for name in user_card_names if name in NAME_TO_CODE}

        route_pairs = getattr(alert, "route_pairs", None)
        chosen_pair = None

        if route_pairs:
            # Шукаємо пари (buy_code, sell_code), де користувач має активні карти для обох сторін
            valid_pairs = []
            for pair in route_pairs:
                if len(pair) == 2:
                    b_code, s_code = pair[0], pair[1]
                    if b_code in allowed_buy_codes and b_code in user_card_codes:
                        if s_code in allowed_sell_codes and s_code in user_card_codes:
                            valid_pairs.append((b_code, s_code))

            if valid_pairs:
                # Використовуємо прораховану пару
                chosen_pair = valid_pairs[0]

        if chosen_pair:
            chosen_buy, chosen_sell = chosen_pair
        else:
            # Fallback на існуючу незалежну логіку з пріоритетом наявного пластику (навіть неактивного)
            final_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
                cls._select_best_bank_from_owned(allowed_buy_names, user_all_cards) or
                (engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank"))
            )
            final_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
                cls._select_best_bank_from_owned(allowed_sell_names, user_all_cards) or
                (engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank"))
            )
            chosen_buy = NAME_TO_CODE.get(final_buy_name, "43")
            chosen_sell = NAME_TO_CODE.get(final_sell_name, "43")

        return chosen_buy, chosen_sell

    def _user_wants(self, user: dict, opp: dict) -> tuple[bool, str, float]:
        """Персональний фільтр юзера. Повертає (True, "", scaled_amount) або (False, причина, entry)."""
        # Спред-алерти йдуть тим, у кого SPREAD серед активних режимів.
        # Раніше перевірялось рівно одне поле, тож увімкнути спред разом із
        # тейкером було неможливо навіть теоретично.
        from core.engine.scanner_helpers import _user_modes

        modes = _user_modes(user)
        entry = float(opp["actual_entry_uah"])
        if "SPREAD" not in modes:
            return False, f"modes={','.join(modes)}", entry

        # 0. Перевірка FOP та Banka/Jar блокування (per-user)
        filter_fop = user.get("filter_fop_tov", "hide")
        filter_banka = user.get("filter_banka_jar", "hide")
        buy_o = opp["buy_order"]
        sell_o = opp["sell_order"]

        # Ціновий фільтр входу.
        #
        # Меню «💲 Фільтр ціни» в боті існувало давно і справно писало
        # price_range_json, але PriceRangeFilter не імпортувався ніде — тобто
        # налаштування зберігалось, бот рапортував «збережено», а жоден ордер
        # за ним не відсіювався. Тепер фільтр працює там, де він і має сенс:
        # у спред-режимі, де власного обмеження по ціні не було взагалі
        # (тейкер-режими мають свої стратегії taker_*_price_strategy).
        #
        # Застосовуємо до ціни КУПІВЛІ: саме за нею ти входиш у зв'язку.
        price_range = user.get("price_range") or {}
        if price_range:
            from filters.price_filter import PriceRangeFilter

            price_filter = PriceRangeFilter(price_range)
            if price_filter.is_active and not price_filter.matches(buy_o):
                return False, f"Ціна входу поза фільтром ({price_filter.describe()})", entry

        mf = user.get("merchant_filters") or {}
        bl_by_id, bl_by_name = user.get("_personal_blacklist") or ({}, {})

        for order_obj in (buy_o, sell_o):
            risk_flags = getattr(order_obj, "risk_flag", "") or ""

            # Особистий бан діє беззастережно: людина сама його поставила,
            # тож blacklist_mode тут не питаємо — він про те, як поводитись
            # зі спільним списком і вердиктами ризик-движка.
            if in_personal_blacklist(order_obj, bl_by_id, bl_by_name):
                return False, f"Особистий чорний список: {order_obj.merchant_name}", entry

            if filter_fop == "hide" and "FOP_TOV_BLOCKED" in risk_flags:
                return False, "FOP_TOV blocked for user", entry
            if filter_banka == "hide" and "BANKA_JAR_BLOCKED" in risk_flags:
                return False, "Banka/Jar blocked for user", entry

            if "BLOCK:BLACKLIST" in risk_flags:
                bl_mode = mf.get("blacklist_mode", "block").lower()
                if bl_mode == "block":
                    return False, f"BLACKLIST merchant {order_obj.merchant_name} blocked for user", entry
            elif "BLOCK" in risk_flags:
                # Всі інші BLOCK вердикти (наприклад, LLM_BLOCK) завжди приховуються
                return False, f"Severe risk BLOCK {order_obj.merchant_name} for user", entry

        user_capital = float(user["capital"])

        # 1. Капітальний коридор з динамічним зменшенням суми (down-scaling)
        scaled_entry = entry
        if entry > user_capital:
            buy_o = opp["buy_order"]
            sell_o = opp["sell_order"]
            buy_min_limit = float(buy_o.min_limit)
            sell_min_limit = float(sell_o.min_limit)
            sell_fiat_estimate = user_capital * (float(sell_o.price) / float(buy_o.price))
            
            if user_capital >= buy_min_limit and sell_fiat_estimate >= sell_min_limit:
                scaled_entry = user_capital
            else:
                return False, f"entry {entry:.0f} > capital {user_capital:.0f} (cannot downscale due to min limits)", entry

        min_amount = float(user.get("min_amount", 0.0))
        if min_amount > 0 and scaled_entry < min_amount:
            return False, f"entry {scaled_entry:.0f} < min_amount {min_amount:.0f}", entry

        # 2. Спред
        spread_strategy = user.get("spread_strategy", "min")
        min_spread = float(user["min_spread"])
        max_spread = float(user.get("max_spread", 0.0))
        net_spread = personal_net_spread(user, opp)

        if spread_strategy == "min":
            if net_spread < min_spread:
                return False, f"spread {net_spread:.2f}% < min {min_spread}", entry
        elif spread_strategy == "max":
            if max_spread > 0 and net_spread > max_spread:
                return False, f"spread {net_spread:.2f}% > max {max_spread}", entry
        elif spread_strategy == "range":
            if net_spread < min_spread:
                return False, f"spread {net_spread:.2f}% < min {min_spread}", entry
            if max_spread > 0 and net_spread > max_spread:
                return False, f"spread {net_spread:.2f}% > max {max_spread}", entry
        elif spread_strategy == "exact":
            if abs(net_spread - min_spread) > 0.05:  # допуск 0.05%
                return False, f"spread {net_spread:.2f}% != exact {min_spread}", entry

        # 3. Нормалізація та звірка перетину банків
        user_buy_normalized = self._clean_and_normalize_banks(resolve_banks(user, "SPREAD", "buy"))
        user_sell_normalized = self._clean_and_normalize_banks(resolve_banks(user, "SPREAD", "sell"))
        opp_buy_normalized = self._clean_and_normalize_banks(opp.get("buy_banks_fit"))
        opp_sell_normalized = self._clean_and_normalize_banks(opp.get("sell_banks_fit"))

        if not (opp_buy_normalized & user_buy_normalized):
            return False, f"buy banks no match: user={user_buy_normalized} opp={opp_buy_normalized}", entry
        if not (opp_sell_normalized & user_sell_normalized):
            return False, f"sell banks no match: user={user_sell_normalized} opp={opp_sell_normalized}", entry

        # 4. Фільтри статистики мерчантів
        from config.defaults import MIN_ORDERS as _DEF_ORDERS, MIN_COMPLETION as _DEF_RATE
        mf = user.get("merchant_filters") or {}
        emf = user.get("exchange_merchant_filters") or {}
        buy_o = opp["buy_order"]
        sell_o = opp["sell_order"]

        for order_obj in (buy_o, sell_o):
            ex_name = getattr(order_obj, "exchange", "")
            side_label = "buy" if order_obj is buy_o else "sell"
            ex_filters = emf.get(ex_name, {})
            # Per-exchange subsidy filter: hide only used subsidies
            used_subs = user.get("_used_subsidies", {})
            if getattr(order_obj, "is_new_user_subsidy", False):
                ex_used = used_subs.get(ex_name, [])
                if "new_user" in ex_used:
                    return False, f"{side_label} new user subsidy already used on {ex_name}", entry
            
            min_orders = float(ex_filters.get("min_orders", 0) or mf.get("min_orders", 0) or _DEF_ORDERS.get(ex_name, 0))
            min_rate = float(ex_filters.get("min_rate", 0.0) or mf.get("min_rate", 0.0) or _DEF_RATE.get(ex_name, 0.0))

            if min_orders > 0 and order_obj.month_order_count < min_orders:
                return False, f"{side_label} merchant orders {order_obj.month_order_count} < {min_orders}", entry
            if min_rate > 0 and order_obj.finish_rate_pct < min_rate:
                return False, f"{side_label} merchant rate {order_obj.finish_rate_pct:.1f}% < {min_rate}", entry

            max_offline = int(ex_filters.get("max_offline_mins") or mf.get("max_offline_mins") or 0)
            if max_offline > 0:
                last_online = getattr(order_obj, "last_online_mins", None)
                if last_online is not None and last_online > max_offline:
                    return False, f"{side_label} merchant last online {last_online}m > {max_offline}m", entry

        return True, "", scaled_entry

    async def wants_which(self, user_id: int, opps: dict[str, dict]) -> dict[str, str]:
        """
        Які зв'язки проходять фільтри користувача, і чому решта — ні.

        Потрібне дашборду: `state.opportunities` — глобальний список того,
        що знайшов сканер, і до нього не застосовано жодного персонального
        фільтра. Тобто на сайті було видно ордери, які цей користувач у
        Telegram не отримав би ніколи — ні за капіталом, ні за спредом, ні
        за банками. Розбіжність між «бачу на сайті» і «приходить у чат»
        пояснити було нічим.

        Рішення саме таке, а не «додати фільтри в API»: причина відсіву
        мусить збігатись із тією, що діє для алертів. Друга копія цих
        перевірок розійшлась би з першою — цей проєкт уже сім разів на
        цьому обпікся, — тож рішення тут ухвалює той самий `_user_wants`.

        Повертає {id: ""} для тих, що проходять, і {id: причина} для решти.
        """
        if not self._db or not opps:
            return {}

        user = await self._db.get_user_by_id(user_id)
        if not user:
            return {}

        # Той самий контекст, що збирає dispatch_batch перед перевіркою:
        # без нього особистий чорний список і субсидії просто не діяли б.
        used_subsidies = await self._db.get_used_subsidies(user_id)
        personal_bl = await self._db._user_blacklist_index(user_id)
        card_settings = await self._db.get_user_card_settings(user_id)
        card_module_enabled = bool(
            card_settings and card_settings.get("card_module_mode") != "off"
        )
        user_buy_names = self._clean_and_normalize_banks(
            resolve_banks(user, "SPREAD", "buy")
        )

        auto_cap_cache: dict[frozenset, float] = {}
        result: dict[str, str] = {}

        for opp_id, opp in opps.items():
            allowed_buy = self._clean_and_normalize_banks(opp.get("buy_banks_fit")) & user_buy_names
            key = frozenset(allowed_buy)
            if key not in auto_cap_cache:
                auto_cap_cache[key] = await self._db.get_user_auto_capital(
                    user_id, allowed_banks=allowed_buy
                )
            auto_cap = auto_cap_cache[key]

            local_user = copy.copy(user)
            if local_user.get("capital_mode") == "auto":
                local_user["capital"] = auto_cap
            elif card_module_enabled and auto_cap > 0:
                local_user["capital"] = min(float(local_user["capital"]), auto_cap)

            local_user["_used_subsidies"] = used_subsidies
            local_user["_personal_blacklist"] = personal_bl

            try:
                wants, reason, _ = self._user_wants(local_user, opp)
            except Exception as e:
                # Помилка перевірки не має ховати зв'язку: краще показати
                # зайве, ніж мовчки прибрати те, що людина мала побачити.
                logger.debug("wants_which %s: %s", opp_id, e)
                wants, reason = True, ""

            result[opp_id] = "" if wants else (reason or "не проходить фільтри")

        return result

    async def dispatch_batch(self, items: list[tuple[SpreadAlert, dict]]) -> None:
        """
        Єдина точка розсилки. Збирає всі підходящі ордери для кожного
        користувача і відправляє їх або поодинці (якщо 1 ордер), або батчем.

        Раніше поруч жив ще й `dispatch()` на один алерт — копія цієї ж логіки
        на 120 рядків, яку сканер не викликав. Розійшлися вони не косметично:
        у dispatch() мутувався спільний закешований dict юзера
        (`user["capital"] = min(...)`), через що капітал "танув" між алертами.
        Тут для кожного алерта береться copy.copy(user).
        """
        if not items:
            return

        users = await self._get_users()
        if not users:
            logger.info("📬 Dispatch fallback → queue (немає зареєстрованих юзерів) | Batch size: %d", len(items))
            for alert, opp in items:
                await self._notifier.push(alert)
            return

        from config.runtime import runtime_config
        show_logs = runtime_config.get("show_spread_logs", "true") == "true"

        for user in users:
            uid = user.get("user_id")
            chat_id = user.get("chat_id")

            # ── Дані юзера, незмінні в межах батчу — тягнемо ОДИН раз ───────
            # Раніше все це смикалось на кожен алерт: при 20 алертах і 4 юзерах
            # виходило ~400 запитів за цикл замість 4.
            is_asym_active = await self._db.get_feature_status(uid, "asymmetric_spread")
            card_settings = await self._db.get_user_card_settings(uid)
            card_module_enabled = bool(card_settings and card_settings.get("card_module_mode") != "off")
            used_subsidies = await self._db.get_used_subsidies(uid) if self._db else {}
            # Особистий чорний список — теж незмінний у межах батчу. Тягнемо
            # індекс один раз: _user_wants синхронний, і робити там запит на
            # кожен ордер означало б сотні звернень до бази за цикл.
            personal_bl = (
                await self._db._user_blacklist_index(uid) if self._db else ({}, {})
            )
            try:
                user_cards_active = await self._db.get_cards(owner_id=uid, status="active")
                user_cards_all = await self._db.get_cards(owner_id=uid)
            except Exception as e:
                logger.warning("Помилка отримання карток користувача %s: %s", uid, e)
                user_cards_active, user_cards_all = [], []

            # auto_capital залежить від набору дозволених банків, а він у різних
            # алертів різний — тому мемоїзуємо по цьому набору.
            auto_cap_cache: dict[frozenset, float] = {}
            user_buy_names = self._clean_and_normalize_banks(
                resolve_banks(user, "SPREAD", "buy")
            )

            user_matches = []  # list of (local_alert, opp, is_sniper)

            for alert, opp in items:
                opp_buy_names = self._clean_and_normalize_banks(opp.get("buy_banks_fit"))
                allowed_buy_names = opp_buy_names & user_buy_names

                cache_key = frozenset(allowed_buy_names)
                if cache_key in auto_cap_cache:
                    auto_cap = auto_cap_cache[cache_key]
                else:
                    auto_cap = await self._db.get_user_auto_capital(uid, allowed_banks=allowed_buy_names)
                    auto_cap_cache[cache_key] = auto_cap

                local_user = copy.copy(user)
                if local_user.get("capital_mode") == "auto":
                    local_user["capital"] = auto_cap
                else:
                    if card_module_enabled and auto_cap > 0:
                        local_user["capital"] = min(float(local_user["capital"]), auto_cap)

                if opp.get("is_asymmetric") and not is_asym_active:
                    continue

                is_sniper = False
                for r in local_user.get("sniper_rules", []):
                    req_ex = r.get("exchange", "")
                    req_dir = r.get("direction", "")
                    min_spd = float(r.get("min_spread", 0))
                    min_vol = float(r.get("min_volume", 0))

                    if float(opp["net_spread_pct"]) >= min_spd and float(opp["actual_entry_uah"]) >= min_vol:
                        if req_dir == "BUY" and opp["sell_order"].exchange.upper() == req_ex.upper():
                            is_sniper = True
                            break
                        elif req_dir == "SELL" and opp["buy_order"].exchange.upper() == req_ex.upper():
                            is_sniper = True
                            break

                local_user["_used_subsidies"] = used_subsidies
                local_user["_personal_blacklist"] = personal_bl

                wants, skip_reason, scaled_amount = self._user_wants(local_user, opp)

                if wants or is_sniper:
                    chosen_buy, chosen_sell = await self.adapt_alert_for_user(
                        self._db, local_user, alert, cards=(user_cards_active, user_cards_all),
                    )

                    local_alert = copy.copy(alert)
                    local_alert.buy_bank = chosen_buy
                    local_alert.sell_bank = chosen_sell
                    local_alert.is_asymmetric = opp.get("is_asymmetric", False)
                    local_alert.asymmetric_details = opp.get("asymmetric_details")

                    original_amount = float(opp["actual_entry_uah"])
                    if scaled_amount < original_amount:
                        ratio = scaled_amount / original_amount if original_amount > 0 else 1.0
                        local_alert.deal_amount_uah = scaled_amount
                        local_alert.profit_uah = alert.profit_uah * ratio
                        if local_alert.is_asymmetric and local_alert.asymmetric_details:
                            local_alert.asymmetric_details = copy.copy(local_alert.asymmetric_details)
                            local_alert.asymmetric_details["buy_required"] = scaled_amount
                            local_alert.asymmetric_details["sell_executed"] = local_alert.asymmetric_details["sell_executed"] * ratio
                    else:
                        local_alert.deal_amount_uah = original_amount

                    user_matches.append((local_alert, opp, is_sniper))

            if not user_matches:
                continue

            # Сортуємо за спредом (найбільший спочатку)
            user_matches.sort(key=lambda x: x[0].spread_pct, reverse=True)

            group_scanner = user.get("group_scanner_alerts", True)

            if len(user_matches) == 1 or not group_scanner:
                # Відправляємо окремими повідомленнями
                for local_alert, opp, is_sniper in user_matches:
                    match_type = "🎯 SNIPER" if is_sniper else "✅ SPREAD"
                    try:
                        await self._notifier.send_to_user(chat_id, local_alert, is_sniper_match=is_sniper)
                        spawn(self._db.save_proposal(
                            buy_exchange=local_alert.buy_order.exchange,
                            sell_exchange=local_alert.sell_order.exchange,
                            buy_merchant=local_alert.buy_order.merchant_name,
                            sell_merchant=local_alert.sell_order.merchant_name,
                            spread_pct=local_alert.spread_pct,
                            profit_uah=local_alert.profit_uah,
                            deal_amount=local_alert.deal_amount_uah,
                            route_type=opp.get("route_type", "SPREAD"),
                            buy_bank=local_alert.buy_bank,
                            sell_bank=local_alert.sell_bank,
                            was_sent=True,
                            user_id=uid,
                        ), "save_proposal", logger_=logger)
                        if show_logs:
                            logger.info("  └─ %s ВІДПРАВЛЕНО (Одиночний) → Юзер: %s", match_type, uid)
                    except Exception as e:
                        logger.warning("  └─ ❌ Помилка відправки одиночного алерта юзеру %s: %s", uid, e)
            else:
                # Відправляємо груповим батчем
                batch_alerts = [m[0] for m in user_matches]
                try:
                    await self._notifier.send_batch_to_user(chat_id, batch_alerts)
                    
                    # Зберігаємо пропозиції для всіх алертів у пачці
                    for local_alert, opp, is_sniper in user_matches:
                        spawn(self._db.save_proposal(
                            buy_exchange=local_alert.buy_order.exchange,
                            sell_exchange=local_alert.sell_order.exchange,
                            buy_merchant=local_alert.buy_order.merchant_name,
                            sell_merchant=local_alert.sell_order.merchant_name,
                            spread_pct=local_alert.spread_pct,
                            profit_uah=local_alert.profit_uah,
                            deal_amount=local_alert.deal_amount_uah,
                            route_type=opp.get("route_type", "SPREAD"),
                            buy_bank=local_alert.buy_bank,
                            sell_bank=local_alert.sell_bank,
                            was_sent=True,
                            user_id=uid,
                        ), "save_proposal", logger_=logger)
                    if show_logs:
                        logger.info("  └─ 📦 BATCH ВІДПРАВЛЕНО (Кількість: %d) → Юзер: %s", len(batch_alerts), uid)
                except Exception as e:
                    logger.warning("  └─ ❌ Помилка відправки BATCH алертів юзеру %s: %s", uid, e)
