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
        tx_count = await self._db.get_card_transactions_count(card_id, hours=24)

        # Визначаємо константи залежно від напрямку
        max_single = limits.get("max_single_tx_out" if direction == "buy" else "max_single_tx_in", 29999.0)
        daily_max = limits.get("daily_out_max" if direction == "buy" else "daily_in_max", 150000.0)
        max_tx = limits.get("max_tx_per_day", 15)

        # Прорахунок проєкції Балансу
        bal_before = c.get("balance", 0.0)
        bal_after = bal_before - tx_amount if direction == "buy" else bal_before + tx_amount

        # Прорахунок проєкції Лімітів обороту
        avail_before = max(0.0, daily_max - used_daily)
        avail_after = max(0.0, avail_before - tx_amount)

        # Прорахунок лічильника транзакцій
        tx_after = tx_count + 1

        drop_text = "Власна" if c.get("is_own") else "Дроп"
        bank_name = c.get("bank_name", "unknown").capitalize()
        last_four = c.get("last_four", "####")
        label_str = f" [{c['label']}]" if c.get("label") else ""

        dir_label = "💸 КУПІВЛЯ (BUY)" if direction == "buy" else "📥 ПРИЙМАЄМО (SELL)"

        text = (
            f"💳 <b>{dir_label}: {bank_name} *{last_four}</b> ({drop_text}{label_str})\n"
            f"  ├ 💰 Баланс у боті: <code>{bal_before:,.2f} ₴</code> ➔ <b>{bal_after:,.2f} ₴</b>\n"
            f"  ├ 🛡️ Одноразовий ліміт TX: <code>{max_single:,.0f} ₴</code> (макс. за один переказ)\n"
            f"  ├ 📅 Добовий ліміт банку: <code>{avail_before:,.0f}/{daily_max:,.0f} ₴</code> вільних ➔ <b>{avail_after:,.0f} ₴</b>\n"
            f"  └ 🔢 Лічильник TX за добу: <code>{tx_count}/{max_tx}</code> операцій ➔ <b>{tx_after}</b>\n"
        )
        return text

    async def get_card_block(
            self,
            chat_id: int,
            target_amount: float,
            direction: str,
            bank: str,
            order_id: str,
            cache_key: str = None,
            buy_card_spent_fiat: float = 0.0,
            buy_card_id: str = None
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

        result = await engine.run(chat_id, bank, target_amount, direction, crypto_available=True, excluded_cards=excluded if excluded else None)

        if result.status in ("no_cards", "disabled"):
            if result.status == "disabled":
                return "", [], None
            text = f"{prefix}⚠️ Немає доступних карток\n"
            if result.rejection_report:
                text += "\n".join([f"  └ *{rep['last_four']} — {rep['reason']}" if "last_four" in rep else f"  └ {rep['reason']}" for rep in result.rejection_report])
            return text, [], None

        if result.status == "no_crypto":
            return f"{prefix}⚠️ Недостатньо крипти для Sell", [], None

        cards = [result.best_card] if result.best_card else (result.split_options[0] if result.status == "needs_split" else [])
        _card_matching_cache[cache_key]["found_cards"] = cards

        if not cards:
            return f"{prefix}⚠️ Немає доступних", [], None

        c = cards[0]
        card_id = c.get("id") or c.get("card_id")
        drop_text = "Власна" if c.get("is_own", 1) else "Дроп"

        # Стягуємо актуальні ліміти банку для побудови проєкції
        limits = await self._db.get_user_bank_limits(chat_id, bank) or {"max_single_tx_out": 29999.0, "max_single_tx_in": 29999.0, "daily_in_max": 150000.0, "daily_out_max": 150000.0, "max_tx_per_day": 15}
        max_single = limits["max_single_tx_in"] if direction == "sell" else limits["max_single_tx_out"]
        daily_max = limits["daily_in_max"] if direction == "sell" else limits["daily_out_max"]
        used_daily = await self._db.get_rolling_used(card_id, direction, hours=24)
        tx_count = await self._db.get_card_transactions_count(card_id, hours=24)

        # 🚀 ПОСЛІДОВНИЙ РОЗРАХУНОК БАЛАНСУ: якщо це та сама карта на Sell-нозі, зменшуємо стартовий баланс
        base_bal = float(c.get("balance", 0.0))
        if direction == "sell" and card_id == buy_card_id:
            base_bal -= buy_card_spent_fiat

        bal_after = base_bal - target_amount if direction == "buy" else base_bal + target_amount
        avail_before = max(0.0, daily_max - used_daily)
        avail_after = max(0.0, avail_before - target_amount)

        is_red_zone = False
        warning_reasons = []

        if tx_count >= limits['max_tx_per_day'] - 2:
            is_red_zone = True
            warning_reasons.append(f"Критично мало транзакцій (залишилось {limits['max_tx_per_day'] - tx_count})")

        if avail_after < daily_max * 0.15:
            is_red_zone = True
            warning_reasons.append("Добовий ліміт банку залишок < 15%")

        # 🚀 ЗБІРКА ТЕКСТУ ЗА РІВНЕМ ДЕТАЛІЗАЦІЇ (Категоризація вмісту)
        detail_level = card_settings.get("card_detail_level", "full")

        if detail_level == "compact":
            # Ультра-короткий вивід для швидкої роботи
            card_info_body = (
                f"  ├ 💰 Баланс: <code>{base_bal:,.0f} ₴</code> ➔ <b>{bal_after:,.0f} ₴</b>\n"
                f"  └ 📅 Ліміт: <code>{avail_before:,.0f} ₴</code> ➔ <b>{avail_after:,.0f} ₴</b>"
            )
        else:
            # Твій повний детальний варіант
            card_info_body = (
                f"  ├ 💰 Баланс у боті: <code>{base_bal:,.2f} ₴</code> ➔ <b>{bal_after:,.2f} ₴</b>\n"
                f"  ├ 🛡️ Одноразовий ліміт TX: <code>{max_single:,.0f} ₴</code>\n"
                f"  ├ 📅 Добовий ліміт банку: <code>{avail_before:,.0f}/{daily_max:,.0f} ₴</code> ➔ <b>{avail_after:,.0f} ₴</b>\n"
                f"  └ 🔢 Лічильник TX за добу: <code>{tx_count}/{limits['max_tx_per_day']}</code> ➔ <b>{tx_count + 1}</b>"
            )

        # Логіка Смарт-спойлера: якщо картка в небезпеці — лишаємо текст повністю ВІДКРИТИМ
        use_smart_spoiler = card_settings.get("enable_smart_spoiler", True)

        if use_smart_spoiler and is_red_zone:
            prefix_header = f"{prefix}<b>{c['bank_name'].capitalize()} *{c['last_four']}</b> ({drop_text}) 🚨\n"
            warn_msg = f"⚠️ <b>РИЗИК ФІНМОНУ: {', '.join(warning_reasons)}</b>\n"
            text = f"{prefix_header}{warn_msg}{card_info_body}"  # Без блокуblockquote!
        else:
            # Звичайний безпечний режим — ховаємо все під спойлер
            prefix_header = f"{prefix}<b>{c['bank_name'].capitalize()} *{c['last_four']}</b> ({drop_text})\n"
            text = f"{prefix_header}<blockquote expandable>{card_info_body}</blockquote>"

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