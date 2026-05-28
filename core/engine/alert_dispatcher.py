# core/engine/alert_dispatcher.py
import copy
import logging
import time

from bot.notifier import TelegramNotifier, SpreadAlert
from config.banks import BankRegistry
from core.storage.merchant_db import MerchantDB

logger = logging.getLogger("Scanner.AlertDispatcher")


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
        """Універсальний куленепробивний нормалізатор брудних даних банку з баз SQLite."""
        if not banks_input:
            return set()
        import re
        raw_strings = []
        input_str = str(banks_input)
        raw_strings = re.findall(r'[a-zA-Z0-9а-яА-ЯіІёЁєЄїЇґҐ]+', input_str)

        normalized = set()
        name_map = {
            "43": "monobank", "mono": "monobank", "monobank": "monobank", "моно": "monobank", "монобанк": "monobank",
            "14": "privatbank", "privat": "privatbank", "privatbank": "privatbank", "приват": "privatbank", "приватбанк": "privatbank",
            "64": "pumb", "pumb": "pumb", "пумб": "pumb",
            "48": "a-bank", "abank": "a-bank", "a-bank": "a-bank", "абанк": "a-bank", "а-банк": "a-bank",
            "553": "izibank", "izi": "izibank", "izibank": "izibank", "ізі": "izibank", "ізібанк": "izibank",
            "328": "sense", "sense": "sense", "sensebank": "sense", "сенс": "sense", "сенсбанк": "sense"
        }
        for s in raw_strings:
            s_low = s.lower()
            if s_low in name_map:
                normalized.add(name_map[s_low])
            else:
                normalized.add(s_low)
        return normalized

    def _user_wants(self, user: dict, opp: dict) -> tuple[bool, str, float]:
        """Персональний фільтр юзера. Повертає (True, "", scaled_amount) або (False, причина, entry)."""
        mode = user.get("scanner_mode", "SPREAD")
        if mode != "SPREAD":
            return False, f"mode={mode}", float(opp["actual_entry_uah"])

        entry = float(opp["actual_entry_uah"])
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

        # 2. Мінімальний спред
        if float(opp["net_spread_pct"]) < float(user["min_spread"]):
            return False, f"spread {opp['net_spread_pct']:.2f}% < min {user['min_spread']}", entry

        # 3. Нормалізація та звірка перетину банків
        user_buy_normalized = self._clean_and_normalize_banks(user.get("buy_bank_codes") or user.get("bank_codes"))
        user_sell_normalized = self._clean_and_normalize_banks(user.get("sell_bank_codes") or user.get("bank_codes"))
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
            min_orders = float(ex_filters.get("min_orders", 0) or mf.get("min_orders", 0) or _DEF_ORDERS.get(ex_name, 0))
            min_rate = float(ex_filters.get("min_rate", 0.0) or mf.get("min_rate", 0.0) or _DEF_RATE.get(ex_name, 0.0))

            if min_orders > 0 and order_obj.month_order_count < min_orders:
                return False, f"{side_label} merchant orders {order_obj.month_order_count} < {min_orders}", entry
            if min_rate > 0 and order_obj.finish_rate_pct < min_rate:
                return False, f"{side_label} merchant rate {order_obj.finish_rate_pct:.1f}% < {min_rate}", entry

        return True, "", scaled_entry
    async def dispatch(self, alert: SpreadAlert, opp: dict) -> None:
        """Відправляє алерт з розумним підбором банку на основі ВСІХ спільних фільтрів."""
        users = await self._get_users()
        if not users:
            logger.info("📬 Dispatch fallback → queue (немає зареєстрованих юзерів)")
            await self._notifier.push(alert)
            return

        from config.runtime import runtime_config
        show_logs = runtime_config.get("show_spread_logs", "true") == "true"

        if show_logs:
            logger.info("🔍 Аналіз розсилки для %d юзерів (Спред: %.2f%%)...", len(users), opp["net_spread_pct"])
        else:
            logger.debug("🔍 Аналіз розсилки для %d юзерів (Спред: %.2f%%)...", len(users), opp["net_spread_pct"])
        matched_count = 0

        for user in users:
            uid = user.get("user_id")
            chat_id = user.get("chat_id")

            # 🚀 Отримуємо авто-капітал та налаштування карт
            user_buy_names = self._clean_and_normalize_banks(user.get("buy_bank_codes") or user.get("bank_codes"))
            opp_buy_names = self._clean_and_normalize_banks(opp.get("buy_banks_fit"))
            allowed_buy_names = opp_buy_names & user_buy_names

            auto_cap = await self._db.get_user_auto_capital(uid, allowed_banks=allowed_buy_names)
            card_settings = await self._db.get_user_card_settings(uid)
            card_module_enabled = card_settings and card_settings.get("card_module_mode") != "off"

            if user.get("capital_mode") == "auto":
                user["capital"] = auto_cap
            else:
                if card_module_enabled and auto_cap > 0:
                    user["capital"] = min(float(user["capital"]), auto_cap)

            if opp.get("is_asymmetric"):
                is_asym_active = await self._db.get_feature_status(uid, "asymmetric_spread")
                if not is_asym_active:
                    continue

            is_sniper = False
            for r in user.get("sniper_rules", []):
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

            wants, skip_reason, scaled_amount = self._user_wants(user, opp)

            if wants or is_sniper:
                matched_count += 1
                match_type = "🎯 SNIPER" if is_sniper else "✅ SPREAD"

                # 💳 Витягуємо назви брендів твоїх АКТИВНИХ пластикових карт
                try:
                    user_cards = await self._db.get_cards(owner_id=uid, status="active")
                    user_card_names = {str(c["bank_name"]).lower() for c in user_cards}
                except Exception as e:
                    logger.warning("Помилка отримання карток користувача %s: %s", uid, e)
                    user_card_names = set()

                # Очищуємо та розгортаємо глобальні списки доступних карт
                user_buy_names = self._clean_and_normalize_banks(user.get("buy_bank_codes") or user.get("bank_codes"))
                user_sell_names = self._clean_and_normalize_banks(user.get("sell_bank_codes") or user.get("bank_codes"))
                opp_buy_names = self._clean_and_normalize_banks(opp.get("buy_banks_fit"))
                opp_sell_names = self._clean_and_normalize_banks(opp.get("sell_banks_fit"))

                allowed_buy_names = opp_buy_names & user_buy_names
                allowed_sell_names = opp_sell_names & user_sell_names

                # Шукаємо перетин: які з дозволених фільтрами банків мерчанта у нас РЕАЛЬНО є в гаманці
                cards_buy_match = allowed_buy_names & user_card_names
                cards_sell_match = allowed_sell_names & user_card_names

                # 🧠 АДАПТИВНИЙ ПРІОРИТЕТ:
                # Якщо є збіг по живих картах (напр. Monobank) — ставимо його.
                # Якщо немає — беремо канонічний код, який виплюнув матчер (opp["buy_bank"])
                engine_buy_list = list(self._clean_and_normalize_banks(opp.get("buy_bank", "43")))
                engine_sell_list = list(self._clean_and_normalize_banks(opp.get("sell_bank", "43")))
                engine_buy_name = engine_buy_list[0] if engine_buy_list else "monobank"
                engine_sell_name = engine_sell_list[0] if engine_sell_list else "monobank"

                NAME_TO_CODE = {"monobank": "43", "privatbank": "14", "pumb": "64", "a-bank": "48", "izibank": "553", "sense": "328"}
                CODE_TO_NAME = {v: k for k, v in NAME_TO_CODE.items()}

                # Отримаємо дозволені коди для Buy та Sell
                allowed_buy_codes = {NAME_TO_CODE[name] for name in allowed_buy_names if name in NAME_TO_CODE}
                allowed_sell_codes = {NAME_TO_CODE[name] for name in allowed_sell_names if name in NAME_TO_CODE}

                # Отримуємо коди живих карток користувача
                user_card_codes = {NAME_TO_CODE[name] for name in user_card_names if name in NAME_TO_CODE}

                route_pairs = opp.get("route_pairs")
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
                        from core.utils.fees import get_calculator
                        
                        best_profit = -999999.0
                        best_pair = None
                        
                        buy_price = float(opp["buy_order"].price)
                        sell_price = float(opp["sell_order"].price)
                        gross_profit = (scaled_amount / buy_price) * sell_price - scaled_amount
                        
                        for b_code, s_code in valid_pairs:
                            try:
                                calc = get_calculator(b_code, s_code, opp["buy_order"].exchange, opp["sell_order"].exchange)
                                _, total_fee, _ = calc.calculate_net(scaled_amount, usdt_price=buy_price)
                                net_profit = gross_profit - total_fee
                                if net_profit > best_profit:
                                    best_profit = net_profit
                                    best_pair = (b_code, s_code)
                            except Exception:
                                pass
                        
                        if best_pair:
                            chosen_pair = best_pair

                if chosen_pair:
                    chosen_buy, chosen_sell = chosen_pair
                else:
                    # Fallback на існуючу незалежну логіку
                    final_buy_name = list(cards_buy_match)[0] if cards_buy_match else (
                        engine_buy_name if engine_buy_name in allowed_buy_names else (list(allowed_buy_names)[0] if allowed_buy_names else "monobank")
                    )
                    final_sell_name = list(cards_sell_match)[0] if cards_sell_match else (
                        engine_sell_name if engine_sell_name in allowed_sell_names else (list(allowed_sell_names)[0] if allowed_sell_names else "monobank")
                    )
                    chosen_buy = NAME_TO_CODE.get(final_buy_name, "43")
                    chosen_sell = NAME_TO_CODE.get(final_sell_name, "43")

                # Створюємо ізольовану копію алерта під користувача
                local_alert = copy.copy(alert)
                local_alert.buy_bank = chosen_buy
                local_alert.sell_bank = chosen_sell
                local_alert.is_asymmetric = opp.get("is_asymmetric", False)
                local_alert.asymmetric_details = opp.get("asymmetric_details")
                
                # Застосовуємо зменшену суму угоди (якщо капітал юзера менший)
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

                try:
                    await self._notifier.send_to_user(chat_id, local_alert, is_sniper_match=is_sniper)
                    if show_logs:
                        logger.info(
                            "  └─ %s ВІДПРАВЛЕНО → Юзер: %s | Картки адаптовано під гаманець: %s ➔ %s",
                            match_type, uid, chosen_buy.upper(), chosen_sell.upper()
                        )
                    else:
                        logger.debug(
                            "  └─ %s ВІДПРАВЛЕНО → Юзер: %s | Картки адаптовано під гаманець: %s ➔ %s",
                            match_type, uid, chosen_buy.upper(), chosen_sell.upper()
                        )
                except Exception as e:
                    logger.warning("  └─ ❌ Помилка відправки юзеру %s: %s", uid, e)
            else:
                logger.debug("  └─ ⏭ ПРОПУЩЕНО → Юзер: %s | Причина: %s", uid, skip_reason)

        if show_logs:
            logger.info("📬 Підсумок розсилки: %d/%d юзерів отримали зв'язку.", matched_count, len(users))
        else:
            logger.debug("📬 Підсумок розсилки: %d/%d юзерів отримали зв'язку.", matched_count, len(users))
