# exchanges/cryptobot_inspector.py
import asyncio
import sys

# КРИТИЧНО: патч event loop ДО будь-якого імпорту pyrogram
if sys.version_info >= (3, 10):
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

import json
import logging
import random

from pyrogram import Client
from pyrogram.raw.functions.contacts import ResolveUsername
from pyrogram.raw.functions.messages import GetBotCallbackAnswer


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("Inspector")

API_ID   = 35838582
API_HASH = "c14f2e2b063f5b1437713f605ee3cf53"

STEPS = [
    "p2p",
    "market-trade-buy",
    "choose-asset-USDT",
    "choose-method-monobank",
]


def serialize_message(msg) -> dict:
    buttons = []
    if msg.reply_markup:
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                buttons.append({
                    "text": btn.text,
                    "callback_data": btn.callback_data,
                    "url": btn.url,
                })
    return {
        "message_id": msg.id,
        "timestamp": msg.date.isoformat() if msg.date else None,
        "text": msg.text,
        "caption": msg.caption,
        "buttons": buttons,
        "raw_text_repr": repr(msg.text),
    }


async def click_and_capture(app: Client, chat_id: int, callback: str, log: list, label: str):
    logger.info("🖱️  Натискаємо: %s (%s)", label, callback)

    # Запам'ятовуємо поточний стан повідомлення ДО кліку
    current_msg = None
    async for msg in app.get_chat_history(chat_id, limit=1):
        current_msg = msg
        break

    if not current_msg:
        logger.warning("⚠️  Немає повідомлень в чаті")
        return

    # Шукаємо кнопку і натискаємо
    clicked = False
    async for msg in app.get_chat_history(chat_id, limit=5):
        if msg.reply_markup:
            for row in msg.reply_markup.inline_keyboard:
                for btn in row:
                    if btn.callback_data == callback:
                        peer = await app.resolve_peer(chat_id)
                        await app.invoke(
                            GetBotCallbackAnswer(
                                peer=peer,
                                msg_id=msg.id,
                                data=callback.encode("utf-8"),
                            )
                        )
                        clicked = True
                        break
            if clicked:
                break

    if not clicked:
        logger.warning("⚠️  Кнопка %r не знайдена!", callback)
        return

    # Чекаємо поки бот відредагує повідомлення
    # Перевіряємо зміну кнопок або тексту
    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.4)
        async for msg in app.get_chat_history(chat_id, limit=1):
            # Порівнюємо першу кнопку — якщо змінилась, бот оновив
            new_first_btn = None
            if msg.reply_markup and msg.reply_markup.inline_keyboard:
                new_first_btn = msg.reply_markup.inline_keyboard[0][0].callback_data

            old_first_btn = None
            if current_msg.reply_markup and current_msg.reply_markup.inline_keyboard:
                old_first_btn = current_msg.reply_markup.inline_keyboard[0][0].callback_data

            if new_first_btn != old_first_btn:
                # Повідомлення оновилось
                data = serialize_message(msg)
                data["step"] = label
                data["triggered_by"] = callback
                log.append(data)

                logger.info("📨 Отримано повідомлення [%s]:", label)
                logger.info("   Текст: %s", (msg.text or "")[:200])
                logger.info("   Кнопок: %d", len(data["buttons"]))
                for btn in data["buttons"][:5]:
                    logger.info("     • %r → %r", btn["text"], btn["callback_data"])
                if len(data["buttons"]) > 5:
                    logger.info("     ... ще %d кнопок", len(data["buttons"]) - 5)
                return
            break

    logger.warning("⚠️  Повідомлення не оновилось за 5 сек [%s]", label)


async def inspect_offer_detail(app: Client, chat_id: int, callback: str, log: list):
    logger.info("🔍 Заходимо в ордер: %s", callback)
    await click_and_capture(app, chat_id, callback, log, f"offer_detail:{callback}")


async def main():
    raw_log = []

    async with Client("inspector_session", api_id=API_ID, api_hash=API_HASH) as app:
        logger.info("✅ Pyrogram підключено")

        # Резолвимо через raw MTProto — обходить локальний кеш
        result = await app.invoke(ResolveUsername(username="CryptoBot"))
        chat_id = result.users[0].id
        logger.info("✅ Бот знайдений: id=%d", chat_id)

        # Відкриваємо чат
        await app.send_message(chat_id, "/start")
        await asyncio.sleep(2.0)
        logger.info("✅ /start відправлено")

        # Крок 1: Навігація buy → USDT → Monobank
        for i, callback in enumerate(STEPS):
            await click_and_capture(app, chat_id, callback, raw_log, f"step_{i}:{callback}")
            await asyncio.sleep(random.uniform(1.0, 2.0))

        # Крок 2: Заходимо в перші 3 ордери
        last_log = raw_log[-1] if raw_log else None
        if last_log:
            offer_buttons = [
                btn for btn in last_log["buttons"]
                if btn["callback_data"]
                and btn["callback_data"].startswith("trade-open-offer-")
            ]
            logger.info("🎯 Знайдено %d offer кнопок", len(offer_buttons))

            for btn in offer_buttons[:3]:
                await inspect_offer_detail(app, chat_id, btn["callback_data"], raw_log)
                await asyncio.sleep(random.uniform(1.5, 3.0))
                await click_and_capture(
                    app, chat_id, "choose-method-monobank", raw_log, "back_to_list"
                )
                await asyncio.sleep(random.uniform(1.0, 2.0))

        # Крок 3: Sell
        logger.info("\n--- ТЕПЕР SELL ---\n")
        sell_steps = [
            ("p2p", "p2p"),
            ("market-trade-sell", "sell"),
            ("choose-asset-USDT", "usdt"),
            ("choose-method-monobank", "mono_sell"),
        ]
        for callback, label in sell_steps:
            await click_and_capture(app, chat_id, callback, raw_log, label)
            await asyncio.sleep(random.uniform(1.0, 2.0))

    # Зберігаємо
    output_path = "cryptobot_raw_log.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(raw_log, f, ensure_ascii=False, indent=2)

    logger.info("💾 Збережено %д записів у %s", len(raw_log), output_path)


if __name__ == "__main__":
    asyncio.run(main())