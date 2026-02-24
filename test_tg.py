import asyncio
from decimal import Decimal
from exchanges.base import Order
from notifications.telegram_notifier import TelegramNotifier, SpreadAlert

async def main():
    notifier = TelegramNotifier()
    await notifier.start()

    buy_order = Order(
        id="111", price=Decimal("43.90"), available_amount=Decimal("100"),
        min_limit=Decimal("1000"), max_limit=Decimal("5000"),
        merchant_id="123", merchant_name="Драб",
        month_order_count=50, finish_rate_pct=98.5,
        link="https://www.bybit.com/fiat/trade/otc/profile/123"
    )
    sell_order = Order(
        id="222", price=Decimal("44.35"), available_amount=Decimal("200"),
        min_limit=Decimal("1000"), max_limit=Decimal("10000"),
        merchant_id="456", merchant_name="Rothschilds",
        month_order_count=120, finish_rate_pct=99.1,
        link="https://www.bybit.com/fiat/trade/otc/profile/456"
    )

    spread_pct = float((sell_order.price - buy_order.price) / buy_order.price * 100)
    profit_uah = 3000 * spread_pct / 100

    # Тест 1: одиночний алерт
    await notifier.push(SpreadAlert(buy_order, sell_order, spread_pct, profit_uah))

    # Тест 2: батч з 6 алертів — має прийти одне зведене повідомлення
    for i in range(6):
        await notifier.push(SpreadAlert(buy_order, sell_order, spread_pct + i * 0.1, profit_uah))

    await asyncio.sleep(5)  # чекаємо поки воркер відправить
    await notifier.stop()

if __name__ == "__main__":
    asyncio.run(main())