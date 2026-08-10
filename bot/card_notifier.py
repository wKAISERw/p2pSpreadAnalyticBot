import time
import logging
from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from core.storage.merchant_db import MerchantDB
from core.engine.card_matching_engine import CardMatchingEngine

logger = logging.getLogger(__name__)

# Cache schema: cache_key -> { "order_id": str, "target_amount": float, "direction": str, "bank": str, "excluded_cards": list[str], "found_cards": list[dict] }
_card_matching_cache = {}


def _clean_cache():
    """TTL-очищення кешу (записи старші 30 хвилин)."""
    now = time.time()
    expired = [k for k in list(_card_matching_cache.keys())
               if now - int(k.split("_")[-1]) > 1800]
    for k in expired:
        del _card_matching_cache[k]


class CardNotifier:
    def __init__(self, db: MerchantDB, bot_instance):
        self._db = db
        self._bot = bot_instance

    async def _render_card_projection(self, card_id: str, direction: str, tx_amount: float) -> str:
        """
        Генерує детальний блок картки із проєкцією лімітів та балансу за принципом: Поточний ➔ Очікуваний.
        Гарантує 100% точність за рахунок прямого запиту актуального стану з SQLite.
        """
        if not self._db or not self._db._db:
            return ""

        # Витягуємо найсвіжіший рядок картки з БД
        async with self._db._db.execute("SELECT * FROM cards WHERE id=?", (card_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return f"⚠️ Картка {card_id} не знайдена в базі даних\n"
            c = dict(row)

        # Зчитуємо ліміти й поточні накопичені лічильники з SQLite
        limits = await self._db.get_card_effective_limits(card_id)
        used_daily = await self._db.get_rolling_used(card_id, direction, hours=24)
        used_monthly = await self._db.get_monthly_used(card_id, direction)
        tx_count = await self._db.get_card_transactions_count(card_id, hours=24)

        # Визначаємо константи залежно від напрямку
        max_single = limits["max_single_tx_out" if direction == "buy" else "max_single_tx_in"]
        daily_max = limits["daily_out_max" if direction == "buy" else "daily_in_max"]
        monthly_max = limits["monthly_out_max" if direction == "buy" else "monthly_in_max"]
        max_tx = limits["max_tx_per_day"]

        # Прорахунок проєкції Балансу
        bal_before = c.get("balance", 0.0)
        bal_after = bal_before - tx_amount if direction == "buy" else bal_before + tx_amount

        # Прорахунок проєкції Лімітів обороту
        dir_word = "OUT" if direction == "buy" else "IN"

        if daily_max == -1 or daily_max == -1.0:
            limit_lbl = "♾️ вільно"
            avail_after_str = "♾️"
        else:
            avail_before = max(0.0, daily_max - used_daily)
            avail_after = max(0.0, avail_before - tx_amount)
            limit_lbl = f"{avail_before:,.0f}/{daily_max:,.0f} ₴ вільно"
            avail_after_str = f"{avail_after:,.0f} ₴"

        if monthly_max == -1 or monthly_max == -1.0:
            monthly_limit_lbl = "♾️ вільно"
            avail_monthly_after_str = "♾️"
        else:
            avail_monthly_before = max(0.0, monthly_max - used_monthly)
            avail_monthly_after = max(0.0, avail_monthly_before - tx_amount)
            monthly_limit_lbl = f"{avail_monthly_before:,.0f}/{monthly_max:,.0f} ₴ вільно"
            avail_monthly_after_str = f"{avail_monthly_after:,.0f} ₴"

        if max_single == -1 or max_single == -1.0:
            max_single_str = "♾️"
        else:
            max_single_str = f"{max_single:,.0f} ₴"

        if max_tx == -1 or max_tx == -1.0:
            max_tx_str = "♾️"
            tx_after_str = "—"
        else:
            max_tx_str = str(max_tx)
            tx_after_str = str(tx_count + 1)

        drop_text = "Власна" if c.get("is_own") else "Дроп"
        is_warmed_up = bool(c.get("is_warmed_up", 0))
        warmth_badge = "🔥 Прогріта" if is_warmed_up else "⚪ Не прогріта"
        bank_name = c.get("bank_name", "unknown").capitalize()
        last_four = c.get("last_four", "####")
        label_str = f" [{c['label']}]" if c.get("label") else ""

        dir_label = "💸 КУПІВЛЯ (BUY)" if direction == "buy" else "📥 ПРИЙМАЄМО (SELL)"

        text = (
            f"💳 <b>{dir_label}: {bank_name} *{last_four}</b> ({drop_text} | {warmth_badge}{label_str})\n"
            f"  ├ 💰 Баланс у боті: <code>{bal_before:,.2f} ₴</code> ➔ <b>{bal_after:,.2f} ₴</b>\n"
            f"  ├ 🛡️ Одноразовий ліміт TX: <code>{max_single_str}</code> (макс. за один переказ)\n"
            f"  ├ 📅 Денний {dir_word} (твій ліміт): <code>{limit_lbl}</code> ➔ <b>{avail_after_str}</b>\n"
            f"  ├ 📆 Місячний {dir_word} (твій ліміт): <code>{monthly_limit_lbl}</code> ➔ <b>{avail_monthly_after_str}</b>\n"
            f"  └ 🔢 Лічильник TX за добу: <code>{tx_count}/{max_tx_str}</code> операцій ➔ <b>{tx_after_str}</b>\n"
        )
        return text

    async def _render_balances_breakdown(self, chat_id: int) -> str:
        """
        Генерує компактну розбивку балансів по всіх активних картках.
        """
        all_cards = await self._db.get_cards(owner_id=chat_id, status="active")
        if not all_cards:
            return ""
        
        lines = ["\n📊 <b>Баланси активних карток:</b>"]
        for c in all_cards:
            is_warmed = bool(c.get("is_warmed_up", 0))
            warmed_icon = "🔥" if is_warmed else "⚪"
            label_part = f" [{c['label']}]" if c.get("label") else ""
            lines.append(f"  ├ 💳 {c['bank_name'].capitalize()} *{c['last_four']}{label_part} ({warmed_icon}): <code>{c['balance']:,.2f} ₴</code>")
        if len(lines) > 1:
            lines[-1] = lines[-1].replace("  ├", "  └")
            return "\n".join(lines) + "\n"
        return ""

    async def _generate_transfer_recommendation(
        self,
        chat_id: int,
        target_bank: str,
        target_amount: float
    ) -> str:
        """
        Пари «звідки → куди», які покривають нестачу на картці потрібного банку.

        Сам розрахунок переїхав у `core/engine/transfer_advice`: він потрібен
        і дашборду, а звідси віддавався одразу готовим HTML — тож на сайті
        картковий блок лишався без порад, які в чат приходили справно.
        Тут тепер лише форматування.
        """
        from core.engine.transfer_advice import suggest_transfers

        found = await suggest_transfers(self._db, chat_id, target_bank, target_amount)
        if not found:
            return ""

        lines = []
        for s in found:
            src_lbl = f"{s.from_bank.capitalize()} *{s.from_last_four}"
            dest_lbl = f"{s.to_bank.capitalize()} *{s.to_last_four}"
            lines.append(
                f"  💡 <b>Порада:</b> Перекажіть "
                f"<code>{s.amount_uah:,.2f} ₴</code> "
                f"з <b>{src_lbl}</b> на <b>{dest_lbl}</b>"
            )
        return (
            "\n💡 <b>Рекомендовані перекази між картками:</b>\n"
            + "\n".join(lines) + "\n"
        )

    async def _build_rejection_diagnosis(
            self,
            chat_id: int,
            bank: str,
            target_amount: float,
            direction: str,
            excluded_cards: list[str] | None = None,
            route_banks: list[str] | None = None,
    ) -> str:
        """
        Повний root-cause звіт: чому жодна картка не підійшла.
        Покриває всі причини відхилення рушія CardMatchingEngine.

        `route_banks` — усі банки маршруту. Без них звіт рахував картки лише
        того банку, який вказав мерчант, і з увімкненими кошиками писав
        «сумарно доступно 4 820 ₴» там, де движок бачив ще 21 000 ₴ на
        Monobank. Тобто сам матчинг працював, а пояснення до нього — ні.
        """
        import time as _t

        if not self._db:
            return "  └ ⚠️ БД недоступна для діагностики\n"

        # ── Рівень 0: модуль вимкнений ─────────────────────────────────────
        card_settings = await self._db.get_user_card_settings(chat_id) or {}
        if card_settings.get("card_module_mode") == "off":
            return "  └ ❌ <b>Причина:</b> картковий модуль вимкнений у налаштуваннях\n"

        # ── Отримуємо всі картки по банках маршруту та загалом ─────────────
        banks_in_route = [b for b in (route_banks or [bank]) if b]
        all_cards_bank: list[dict] = []
        seen_ids: set = set()
        for b in banks_in_route:
            for card in await self._db.get_cards(owner_id=chat_id, bank_name=b):
                if card["id"] not in seen_ids:
                    seen_ids.add(card["id"])
                    all_cards_bank.append(card)
        all_cards_any  = await self._db.get_cards(owner_id=chat_id)

        # ── Рівень 1: картки цього банку відсутні взагалі ──────────────────
        if not all_cards_bank:
            other_banks = sorted({c.get("bank_name", "").capitalize() for c in all_cards_any if c.get("bank_name")})
            hint = f" (є картки: {', '.join(other_banks)})" if other_banks else " (картковий модуль порожній)"
            return (
                f"  └ ❌ <b>Причина:</b> немає жодної картки банку <b>{bank.capitalize()}</b>{hint}\n"
                f"  └ 💡 Додайте картку через /cards → Додати картку\n"
            )

        # ── Рівень 2: всі картки неактивні ─────────────────────────────────
        active_cards = [c for c in all_cards_bank if c.get("status") == "active"]
        if not active_cards:
            lines = ["  └ ❌ <b>Причина:</b> всі картки банку неактивні"]
            for c in all_cards_bank:
                st = c.get("status", "unknown")
                icon = {"inactive": "😴", "blocked": "🚫", "frozen": "🧊"}.get(st, "❓")
                lines.append(f"  │   └ 💳 *{c.get('last_four','????')} — {icon} <code>{st}</code>")
            lines.append("  └ 💡 Активуйте картку через /cards")
            return "\n".join(lines) + "\n"

        # ── Рівень 3: всі excluded (юзер натиснув "Інша" до кінця) ────────
        if excluded_cards:
            active_ids = {c.get("id") or c.get("card_id") for c in active_cards}
            if active_ids and active_ids.issubset(set(excluded_cards)):
                return (
                    "  └ ❌ <b>Причина:</b> всі картки банку були виключені кнопкою «Інша»\n"
                    "  └ 💡 Новий алерт скине список виключень автоматично\n"
                )

        # ── Рівень 4: детальна перевірка кожної активної картки ────────────
        lines: list[str] = []
        reasons_summary: dict[str, int] = {}

        # Накопичуємо доступний баланс/ліміт для аналізу спліту
        split_candidates: list[dict] = []  # картки що могли б піти в спліт

        for c in active_cards:
            card_id   = c.get("id") or c.get("card_id")
            last_four = c.get("last_four", "????")
            balance   = float(c.get("balance", 0.0))
            label     = f" [{c['label']}]" if c.get("label") else ""
            drop_text = "Власна" if c.get("is_own") else "Дроп"
            card_label = f"*{last_four}{label} ({drop_text})"

            # Пропускаємо excluded
            if excluded_cards and card_id in excluded_cards:
                lines.append(f"  ├ 💳 {card_label}: ⏭️ виключена кнопкою «Інша»")
                continue

            card_issues: list[str] = []
            try:
                # Отримуємо ефективні ліміти для конкретної картки
                limits = await self._db.get_card_effective_limits(card_id)
                max_single  = limits["max_single_tx_in" if direction == "sell" else "max_single_tx_out"]
                daily_max   = limits["daily_in_max" if direction == "sell" else "daily_out_max"]
                monthly_max = limits["monthly_in_max" if direction == "sell" else "monthly_out_max"]
                max_tx      = limits["max_tx_per_day"]

                # 1. Cooldown
                cooldown_until = float(c.get("cooldown_until", 0))
                if cooldown_until > _t.time():
                    mins_left = max(1, int((cooldown_until - _t.time()) / 60) + 1)
                    card_issues.append(f"⏳ Кулдаун ще <code>{mins_left} хв</code>")
                    reasons_summary["cooldown"] = reasons_summary.get("cooldown", 0) + 1

                # 1b. Card Warmup Limits Check
                tx_count_total, last_tx_ts = await self._db.get_card_warmth_stats(card_id)
                warmup_limit = self._db.get_card_warmup_limit(c, tx_count_total, last_tx_ts, _t.time())
                if warmup_limit is not None and target_amount > warmup_limit:
                    card_issues.append(
                        f"🌡️ Непрогріта картка (ліміт прогріву <code>{warmup_limit:,.0f} ₴</code>)"
                    )
                    reasons_summary["warmup_limit"] = reasons_summary.get("warmup_limit", 0) + 1

                # 2. Баланс (BUY) — нуль = тверде відхилення; > 0 але < суми = кандидат на спліт
                if direction == "buy":
                    if balance == 0:
                        card_issues.append("💰 Баланс: <code>0 ₴</code> — картка порожня")
                        reasons_summary["balance_zero"] = reasons_summary.get("balance_zero", 0) + 1
                    elif balance < target_amount:
                        shortage = target_amount - balance
                        card_issues.append(
                            f"💰 Баланс: <code>{balance:,.0f} ₴</code> — не вистачає <b>{shortage:,.0f} ₴</b> (кандидат у спліт)"
                        )
                        reasons_summary["balance_low"] = reasons_summary.get("balance_low", 0) + 1

                # 3. Ліміт TX за добу
                tx_count = await self._db.get_card_transactions_count(card_id, hours=24)
                if max_tx != -1 and max_tx != -1.0 and tx_count >= max_tx:
                    card_issues.append(f"🔢 Ліміт TX за добу: <code>{tx_count}/{max_tx}</code> — вичерпано")
                    reasons_summary["tx_count"] = reasons_summary.get("tx_count", 0) + 1

                # 4. Добовий ліміт
                used_daily = await self._db.get_rolling_used(card_id, direction, hours=24)
                if daily_max != -1 and daily_max != -1.0:
                    avail_daily = daily_max - used_daily
                    if avail_daily <= 0:
                        card_issues.append(
                            f"📅 Добовий ліміт: <code>0/{daily_max:,.0f} ₴</code> — повністю вичерпано"
                        )
                        reasons_summary["daily_full"] = reasons_summary.get("daily_full", 0) + 1
                    elif target_amount > avail_daily:
                        card_issues.append(
                            f"📅 Добовий залишок: <code>{avail_daily:,.0f}/{daily_max:,.0f} ₴</code>"
                            f" — не вистачає <b>{target_amount - avail_daily:,.0f} ₴</b>"
                        )
                        reasons_summary["daily_low"] = reasons_summary.get("daily_low", 0) + 1
                else:
                    avail_daily = float('inf')

                # 5. Місячний ліміт
                used_monthly = await self._db.get_monthly_used(card_id, direction)
                if monthly_max != -1 and monthly_max != -1.0:
                    avail_monthly = monthly_max - used_monthly
                    if avail_monthly <= 0:
                        card_issues.append(
                            f"📆 Місячний ліміт: <code>0/{monthly_max:,.0f} ₴</code> — вичерпано"
                        )
                        reasons_summary["monthly_full"] = reasons_summary.get("monthly_full", 0) + 1
                    elif target_amount > avail_monthly:
                        card_issues.append(
                            f"📆 Місячний залишок: <code>{avail_monthly:,.0f}/{monthly_max:,.0f} ₴</code>"
                            f" — не вистачає <b>{target_amount - avail_monthly:,.0f} ₴</b>"
                        )
                        reasons_summary["monthly_low"] = reasons_summary.get("monthly_low", 0) + 1
                else:
                    avail_monthly = float('inf')

                # 6. Ліміт одного TX
                if max_single != -1 and max_single != -1.0 and target_amount > max_single:
                    card_issues.append(
                        f"🛡️ Ліміт TX: <code>{max_single:,.0f} ₴</code>"
                        f" — перевищено на <b>{target_amount - max_single:,.0f} ₴</b>"
                    )
                    reasons_summary["max_single"] = reasons_summary.get("max_single", 0) + 1

                # Збираємо кандидатів на спліт (є хоч якийсь доступний ліміт)
                avail_for_split = min(avail_daily, avail_monthly, max_single)
                if warmup_limit is not None:
                    avail_for_split = min(avail_for_split, warmup_limit)
                if direction == "buy":
                    avail_for_split = min(avail_for_split, balance)
                if avail_for_split > 0 and (max_tx == -1 or tx_count < max_tx) and cooldown_until <= _t.time():
                    split_candidates.append({"last_four": last_four, "avail": avail_for_split})

            except Exception as ex:
                card_issues.append(f"⚠️ Помилка діагностики: {ex}")

            if card_issues:
                lines.append(f"  ├ 💳 {card_label}:")
                for issue in card_issues:
                    lines.append(f"  │   └ {issue}")
            else:
                lines.append(f"  ├ 💳 {card_label}: ✅ ліміти ОК — відхилено рушієм (невідома причина)")

        # ── Аналіз спліту: чи могли б картки вкрити суму разом ────────────
        split_note = ""
        if split_candidates:
            total_split_avail = sum(s["avail"] for s in split_candidates)
            if total_split_avail >= target_amount:
                names = ", ".join(f"*{s['last_four']}" for s in split_candidates)
                split_note = (
                    f"  └ 🔀 <b>Спліт теоретично можливий</b> ({names})"
                    f" — разом <code>{total_split_avail:,.0f} ₴</code>, але рушій не зміг зібрати комбінацію"
                    f" (перевірте max_cards_per_order у налаштуваннях)\n"
                )
            else:
                split_note = (
                    f"  └ ❌ <b>Спліт неможливий</b> — сумарно доступно лише"
                    f" <code>{total_split_avail:,.0f} ₴</code>"
                    f" з потрібних <code>{target_amount:,.0f} ₴</code>\n"
                )

        # ── Root-cause заголовок ────────────────────────────────────────────
        total = len(active_cards)
        root_parts = []
        if reasons_summary.get("balance_zero"):
            root_parts.append(f"порожній баланс ({reasons_summary['balance_zero']}/{total})")
        if reasons_summary.get("balance_low"):
            root_parts.append(f"недостатній баланс ({reasons_summary['balance_low']}/{total})")
        if reasons_summary.get("tx_count"):
            root_parts.append(f"ліміт TX за добу ({reasons_summary['tx_count']}/{total})")
        if reasons_summary.get("daily_full"):
            root_parts.append(f"добовий ліміт вичерпано ({reasons_summary['daily_full']}/{total})")
        if reasons_summary.get("daily_low"):
            root_parts.append(f"добовий ліміт замалий ({reasons_summary['daily_low']}/{total})")
        if reasons_summary.get("monthly_full"):
            root_parts.append(f"місячний ліміт вичерпано ({reasons_summary['monthly_full']}/{total})")
        if reasons_summary.get("monthly_low"):
            root_parts.append(f"місячний ліміт замалий ({reasons_summary['monthly_low']}/{total})")
        if reasons_summary.get("max_single"):
            root_parts.append(f"ліміт одного TX ({reasons_summary['max_single']}/{total})")
        if reasons_summary.get("cooldown"):
            root_parts.append(f"кулдаун ({reasons_summary['cooldown']}/{total})")
        if reasons_summary.get("warmup_limit"):
            root_parts.append(f"ліміт прогріву ({reasons_summary['warmup_limit']}/{total})")

        if root_parts:
            header = "  └ ❌ <b>Причина:</b> " + ", ".join(root_parts) + "\n"
        else:
            header = "  └ ❓ <b>Причина невідома</b> — всі ліміти ОК, але рушій відхилив\n"

        card_lines = "\n".join(lines) + "\n" if lines else ""
        
        # Read user display settings to decide if we append balance breakdown and transfer tips
        card_settings = await self._db.get_user_card_settings(chat_id) or {}
        show_breakdown = bool(card_settings.get("show_balances_breakdown", 1))
        show_tips = bool(card_settings.get("show_transfer_tips", 1))
        
        breakdown_text = ""
        if show_breakdown:
            breakdown_text = await self._render_balances_breakdown(chat_id)
            
        tips_text = ""
        if show_tips:
            tips_text = await self._generate_transfer_recommendation(chat_id, bank, target_amount)
            
        return header + card_lines + split_note + breakdown_text + tips_text

    async def get_card_block(
            self,
            chat_id: int,
            target_amount: float,
            direction: str,
            bank: str,
            order_id: str,
            cache_key: str = None,
            buy_card_spent_fiat: float = 0.0,
            buy_card_id: str = None,
            show_breakdown: bool = True,
            route_banks: Optional[list] = None,
            declared_banks: Optional[list] = None,
    ) -> tuple[str, list, Optional[dict]]:
        """
        Повертає (text_block, keyboard_rows, chosen_card_dict) для вбудовування в алерт.
        Реалізує точний послідовний розрахунок балансу 'Було ➔ Стане'.
        """
        _clean_cache()
        if not self._db:
            return "", [], None

        # 🚀 ФІКС: Читаємо налаштування карткової таблиці
        card_settings = await self._db.get_user_card_settings(chat_id) or {}

        # Видаляємо стару забаговану перевірку settings.get("card_module_mode") != "full",
        # оскільки наявність активних карток у базі та прапорці виводу вже є прямим дозволом на роботу.
        prefix = "💸 <b>КУПІВЛЯ (BUY):</b> " if direction == "buy" else "📥 <b>ПРИЙМАЄМО (SELL):</b> "

        engine = CardMatchingEngine(self._db)
        excluded = _card_matching_cache[cache_key].get("excluded_cards", []) if (cache_key and cache_key in _card_matching_cache) else []

        if not cache_key:
            cache_key = f"cm_{order_id[:10]}_{int(time.time())}"
            _card_matching_cache[cache_key] = {"order_id": order_id, "target_amount": target_amount, "direction": direction, "bank": bank, "excluded_cards": [], "found_cards": []}

        # Маршрут може виходити за межі банку мерчанта — див. етап 3 плану
        # й фічі /features → КАРТКИ. Список банків збирає викликач (сканер),
        # тут лише передаємо його далі разом із заявленими банками ордера.
        result = await engine.run(
            chat_id, bank, target_amount, direction, crypto_available=True,
            excluded_cards=excluded if excluded else None,
            banks=route_banks, declared_banks=declared_banks,
        )

        if result.status in ("no_cards", "disabled"):
            if result.status == "disabled":
                return "", [], None
            text = f"{prefix}⚠️ Немає доступних карток\n"
            diagnosis = await self._build_rejection_diagnosis(
                chat_id, bank, target_amount, direction,
                excluded_cards=excluded, route_banks=route_banks,
            )
            text += diagnosis
            return text, [], None

        if result.status == "no_crypto":
            return f"{prefix}⚠️ Недостатньо крипти для Sell", [], None

        cards = [result.best_card] if result.best_card else (result.split_options[0] if result.status == "needs_split" else [])
        _card_matching_cache[cache_key]["found_cards"] = cards

        if not cards:
            text = f"{prefix}⚠️ Немає доступних (split failed)\n"
            diagnosis = await self._build_rejection_diagnosis(
                chat_id, bank, target_amount, direction,
                excluded_cards=excluded, route_banks=route_banks,
            )
            text += diagnosis
            return text, [], None

        # Маршрут виходить за межі того, що мерчант заявив в оголошенні.
        # Це не помилка — мерчанти часто перелічують не всі банки, які
        # приймають, — але це й не узгоджено. Питання «чи можна з ПУМБ?»
        # коштує одного повідомлення в чаті, а неузгоджений переказ —
        # апеляції, тож попередження стоїть ПЕРЕД кнопкою «взяти».
        confirm_block = ""
        if result.needs_confirmation_banks:
            from config.banks import bank_display_name

            names = ", ".join(bank_display_name(b) for b in result.needs_confirmation_banks)
            confirm_block = (
                f"❓ <b>Спитайте в чаті перед оплатою:</b> мерчант не вказував "
                f"<b>{names}</b> в оголошенні. Уточніть, чи приймає переказ звідти.\n"
            )

        c = cards[0]
        card_id = c.get("id") or c.get("card_id")
        drop_text = "Власна" if c.get("is_own", 1) else "Дроп"
        is_warmed_up = bool(c.get("is_warmed_up", 0))
        warmth_badge = "🔥 Прогріта" if is_warmed_up else "⚪ Не прогріта"

        # Стягуємо актуальні ліміти для побудови проєкції (ефективні з урахуванням локальних)
        limits = await self._db.get_card_effective_limits(card_id)
        max_single = limits["max_single_tx_in" if direction == "sell" else "max_single_tx_out"]
        daily_max = limits["daily_in_max" if direction == "sell" else "daily_out_max"]
        monthly_max = limits["monthly_in_max" if direction == "sell" else "monthly_out_max"]
        max_tx = limits["max_tx_per_day"]

        used_daily = await self._db.get_rolling_used(card_id, direction, hours=24)
        used_monthly = await self._db.get_monthly_used(card_id, direction)
        tx_count = await self._db.get_card_transactions_count(card_id, hours=24)

        # 🚀 ПОСЛІДОВНИЙ РОЗРАХУНОК БАЛАНСУ: якщо це та сама карта на Sell-нозі, зменшуємо стартовий баланс
        base_bal = float(c.get("balance", 0.0))
        if direction == "sell" and card_id == buy_card_id:
            base_bal -= buy_card_spent_fiat

        bal_after = base_bal - target_amount if direction == "buy" else base_bal + target_amount

        # Прорахунок проєкції Лімітів обороту
        dir_word = "OUT" if direction == "buy" else "IN"

        if daily_max == -1 or daily_max == -1.0:
            limit_lbl = "♾️ вільно"
            avail_after_str = "♾️"
            avail_after = float('inf')
        else:
            avail_before = max(0.0, daily_max - used_daily)
            avail_after = max(0.0, avail_before - target_amount)
            limit_lbl = f"{avail_before:,.0f}/{daily_max:,.0f} ₴ вільно"
            avail_after_str = f"{avail_after:,.0f} ₴"

        if monthly_max == -1 or monthly_max == -1.0:
            monthly_limit_lbl = "♾️ вільно"
            avail_monthly_after_str = "♾️"
        else:
            avail_monthly_before = max(0.0, monthly_max - used_monthly)
            avail_monthly_after = max(0.0, avail_monthly_before - target_amount)
            monthly_limit_lbl = f"{avail_monthly_before:,.0f}/{monthly_max:,.0f} ₴ вільно"
            avail_monthly_after_str = f"{avail_monthly_after:,.0f} ₴"

        if max_single == -1 or max_single == -1.0:
            max_single_str = "♾️"
        else:
            max_single_str = f"{max_single:,.0f} ₴"

        if max_tx == -1 or max_tx == -1.0:
            max_tx_str = "♾️"
            tx_after_str = "—"
        else:
            max_tx_str = str(max_tx)
            tx_after_str = str(tx_count + 1)

        is_red_zone = False
        warning_reasons = []

        if max_tx != -1 and max_tx != -1.0 and tx_count >= max_tx - 2:
            is_red_zone = True
            warning_reasons.append(f"Критично мало транзакцій (залишилось {max_tx - tx_count})")

        if daily_max != -1 and daily_max != -1.0 and avail_after < daily_max * 0.15:
            is_red_zone = True
            warning_reasons.append("Добовий ліміт банку залишок < 15%")

        # 🚀 ЗБІРКА ТЕКСТУ ЗА РІВНЕМ ДЕТАЛІЗАЦІЇ (Категоризація вмісту)
        detail_level = card_settings.get("card_detail_level", "full")

        if detail_level == "compact":
            # Ультра-короткий вивід для швидкої роботи
            card_info_body = (
                f"  ├ 💰 Баланс: <code>{base_bal:,.0f} ₴</code> ➔ <b>{bal_after:,.0f} ₴</b>\n"
                f"  └ 📅 Ліміт {dir_word}: <code>{limit_lbl}</code> ➔ <b>{avail_after_str}</b>"
            )
        else:
            # Твій повний детальний варіант
            card_info_body = (
                f"  ├ 💰 Баланс у боті: <code>{base_bal:,.2f} ₴</code> ➔ <b>{bal_after:,.2f} ₴</b>\n"
                f"  ├ 🛡️ Одноразовий ліміт TX: <code>{max_single_str}</code>\n"
                f"  ├ 📅 Денний {dir_word} (твій ліміт): <code>{limit_lbl}</code> ➔ <b>{avail_after_str}</b>\n"
                f"  ├ 📆 Місячний {dir_word} (твій ліміт): <code>{monthly_limit_lbl}</code> ➔ <b>{avail_monthly_after_str}</b>\n"
                f"  └ 🔢 Лічильник TX за добу: <code>{tx_count}/{max_tx_str}</code> ➔ <b>{tx_after_str}</b>"
            )

        # Логіка Смарт-спойлера: якщо картка в небезпеці — лишаємо текст повністю ВІДКРИТИМ
        use_smart_spoiler = card_settings.get("enable_smart_spoiler", True)

        if use_smart_spoiler and is_red_zone:
            prefix_header = f"{prefix}<b>{c['bank_name'].capitalize()} *{c['last_four']}</b> ({drop_text} | {warmth_badge}) 🚨\n"
            warn_msg = f"⚠️ <b>РИЗИК ФІНМОНУ: {', '.join(warning_reasons)}</b>\n"
            text = f"{prefix_header}{warn_msg}{confirm_block}{card_info_body}"  # Без блокуblockquote!
        else:
            # Звичайний безпечний режим — ховаємо все під спойлер.
            # Попередження про неузгоджений банк лишається ЗОВНІ спойлера:
            # питання до мерчанта має бути видно без розгортання.
            prefix_header = f"{prefix}<b>{c['bank_name'].capitalize()} *{c['last_four']}</b> ({drop_text} | {warmth_badge})\n"
            text = f"{prefix_header}{confirm_block}<blockquote expandable>{card_info_body}</blockquote>"

        # Check settings to decide if we append balance breakdown
        show_breakdown_config = bool(card_settings.get("show_balances_breakdown", 1))
        if show_breakdown and show_breakdown_config:
            breakdown_text = await self._render_balances_breakdown(chat_id)
            if breakdown_text:
                text += breakdown_text

        rows = [[InlineKeyboardButton(text="✅ Взяти в роботу", callback_data=f"card_match:confirm:{cache_key}"),
                 InlineKeyboardButton(text="🔄 Інша картка", callback_data=f"card_match:other:{cache_key}")]]

        return text, rows, c

    async def send_card_recommendation(
            self, chat_id: int, target_amount: float, direction: str, bank: str, order_id: str,
            cache_key: str = None, message_id: int = None
    ) -> None:
        """Редагує або надсилає картковий блок окремим повідомленням."""
        text, rows = await self.get_card_block(
            chat_id, target_amount, direction, bank, order_id, cache_key
        )
        if not text:
            return

        kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

        try:
            if message_id:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=kb,
                )
            else:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=kb,
                    disable_notification=True,
                )
        except Exception as e:
            logger.error("Error sending card recommendation: %s", e)