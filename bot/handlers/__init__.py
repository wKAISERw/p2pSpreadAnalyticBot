# bot/handlers/__init__.py
# Реєструє всі sub-routers в головному router + UX-пастка для Safe Boot.
from __future__ import annotations
import logging
from aiogram import Router
from aiogram.types import CallbackQuery, Message

from bot.handlers import monitoring, exchanges, filters, trading, system, cards, risk

logger = logging.getLogger("Main")


def get_router() -> Router:
    """Повертає головний router зі всіма підключеними handlers."""
    root = Router()

    # Підключаємо модульні хендлери
    root.include_router(monitoring.router)
    root.include_router(exchanges.router)
    root.include_router(filters.router)
    root.include_router(trading.router)
    root.include_router(system.router)
    root.include_router(cards.router)
    root.include_router(risk.router)

    # Створюємо окремий роутер для фолбеків, щоб вони не перехоплювали запити до модульних роутерів.
    # Aiogram перевіряє хендлери самого роутера перед тим, як спускатися в його sub-routers.
    fallback_router = Router()

    # 🕵️‍♂️ РОЗУМНА ПАСТКА ДЛЯ БОЙОВИХ КНОПОК ТА СЕЙФ-БУТУ
    @fallback_router.callback_query()
    async def global_ux_fallback(call: CallbackQuery):
        """
        Ловить будь-який нерозпізнаний клік.
        Якщо це кнопка Safe Boot — примусово малює Головне меню, щоб розблокувати інтерфейс.
        """
        data = call.data or ""
        print(f"\n🚨 [UX DIAGNOSTIC] Спіймали клік: callback_data='{data}' від {call.from_user.id}\n")
        logger.warning(f"🕵️‍♂️ НЕОБРОБЛЕНИЙ КЛІК: callback_data='{data}'")

        # Перевіряємо, чи це кнопка аварійного буту / сесій
        target_triggers = ["boot", "session", "ignore", "continue", "skip", "safe"]
        if any(trigger in data.lower() for trigger in target_triggers) or data == "menu:main":
            from bot.keyboards.menu import main_menu_kb
            from bot.handlers.core import _generate_dashboard_text, _is_admin

            # Генеруємо текст дашборду та примусово рендеримо нове меню на 5 розділів
            text, _ = await _generate_dashboard_text(call.from_user.id)
            is_admin = _is_admin(call.from_user.id)

            try:
                await call.message.edit_text(
                    text=text,
                    reply_markup=main_menu_kb(is_admin=is_admin)
                )
                await call.answer("🚀 Вхід розблоковано! Ласкаво просимо в Arbix Quantum.")
                return
            except Exception as e:
                logger.error(f"Помилка примусового рендеру меню: {e}")

        # Дефолтна відповідь для інших непідключених кнопок
        if any(data.startswith(p) for p in ["tsell_", "tbuy_", "ad:", "mkord:", "sl:", "fb:"]):
            await call.answer(
                "⚠️ Запит застарів (або бот був перезапущений).\n"
                "Будь ласка, відкрийте налаштування або алерт заново.",
                show_alert=True
            )
            return

        await call.answer(f"⚠️ Кнопка '{data}' ще в процесі редизайну Фази 3", show_alert=True)

    @fallback_router.message()
    async def global_message_fallback(message: Message):
        """Ловить неочікуваний текст або команди."""
        logger.warning(f"🕵️‍♂️ НЕОБРОБЛЕНА КОМАНДА/ТЕКСТ: '{message.text}'")

    root.include_router(fallback_router)

    return root