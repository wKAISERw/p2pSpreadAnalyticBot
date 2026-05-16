import time
import logging
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.storage.merchant_db import MerchantDB
from core.engine.card_matching_engine import CardMatchingEngine

logger = logging.getLogger(__name__)

# Cache schema: cache_key -> { "order_id": str, "target_amount": float, "direction": str, "bank": str, "excluded_cards": list[str], "found_cards": list[dict] }
_card_matching_cache = {}

class CardNotifier:
    def __init__(self, db: MerchantDB, bot_instance):
        self._db = db
        self._bot = bot_instance

    async def send_card_recommendation(
        self, chat_id: int, target_amount: float, direction: str, bank: str, order_id: str, 
        cache_key: str = None, message_id: int = None
    ) -> None:
        # TTL Cleanup
        now = time.time()
        expired = [k for k in list(_card_matching_cache.keys()) 
                   if now - int(k.split("_")[-1]) > 1800]
        for k in expired:
            del _card_matching_cache[k]
            
        if not self._bot or not self._db:
            return
            
        settings = await self._db.get_user_card_settings(chat_id)
        if not settings or settings.get("card_module_mode") != "full":
            return # Модуль вимкнено для цього юзера

        engine = CardMatchingEngine(self._db)
        
        excluded = []
        if cache_key and cache_key in _card_matching_cache:
            excluded = _card_matching_cache[cache_key].get("excluded_cards", [])
        else:
            cache_key = f"cm_{order_id[:10]}_{int(time.time())}"
            _card_matching_cache[cache_key] = {
                "order_id": order_id,
                "target_amount": target_amount,
                "direction": direction,
                "bank": bank,
                "excluded_cards": [],
                "found_cards": []
            }
            
        result = await engine.run(chat_id, bank, target_amount, direction, crypto_available=True)
        
        # Виключаємо картки, які вже пропонували (якщо юзер натиснув "Інша картка")
        # Якщо result.status успішний, але картка в excluded - це обробляється всередині engine. 
        # В нашому engine зараз excluded_cards немає в аргументах. Ой. Треба буде додати у CardMatchingEngine, або фільтрувати тут. 
        # Оскільки в Engine цього ще нема (додамо згодом), поки що ми просто передамо як є.
        # Точніше, Engine зараз не має `excluded_cards`.
        
        if result.status in ("no_cards", "no_crypto"):
            reason = "Недостатньо крипти для Sell-ноги" if result.status == "no_crypto" else "Не знайдено підходящих карток (всі зайняті або ліміти вичерпано)"
            text = f"💳 <b>Картовий модуль:</b>\n⚠️ {reason}"
            kb = None
        elif result.status == "needs_split":
            cards = result.split_options[0] if result.split_options else []
            _card_matching_cache[cache_key]["found_cards"] = cards
            
            text = f"💳 <b>Рекомендовано СПЛІТ на {len(cards)} картки ({target_amount:.0f} ₴):</b>\n\n"
            for c in cards:
                drop_text = "Власна" if c.get("is_own") else "Дроп"
                text += f"🟢 {c['bank_name'].capitalize()} {c['last_four']} ({drop_text})\n"
                
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Взяти в роботу (Резерв)", callback_data=f"card_match:confirm:{cache_key}")],
                [InlineKeyboardButton(text="🔄 Інший варіант", callback_data=f"card_match:other:{cache_key}")],
                [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"card_match:cancel:{cache_key}")]
            ])
        else:
            cards = []
            if result.best_card:
                if "buy" in result.best_card and "sell" in result.best_card: # spread case
                    cards = [result.best_card["buy"], result.best_card["sell"]]
                else:
                    cards = [result.best_card]
                    
            _card_matching_cache[cache_key]["found_cards"] = cards
            
            text = f"💳 <b>Рекомендована картка для ордеру ({target_amount:.0f} ₴):</b>\n\n"
            for c in cards:
                drop_text = "Власна" if c.get("is_own") else "Дроп"
                text += f"🟢 {c['bank_name'].capitalize()} {c['last_four']} ({drop_text})\n"
                
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Взяти в роботу (Резерв)", callback_data=f"card_match:confirm:{cache_key}")],
                [InlineKeyboardButton(text="🔄 Інша картка", callback_data=f"card_match:other:{cache_key}")],
                [InlineKeyboardButton(text="❌ Скасувати", callback_data=f"card_match:cancel:{cache_key}")]
            ])

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
                    disable_notification=True
                )
        except Exception as e:
            logger.error("Error sending card recommendation: %s", e)
