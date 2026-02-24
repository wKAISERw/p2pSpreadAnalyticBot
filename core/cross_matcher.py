from decimal import Decimal, getcontext
import logging
from exchanges.base import Order
from core.fees import get_calculator

getcontext().prec = 28
logger = logging.getLogger("CrossMatcher")


class CrossMatchingEngine:
    def __init__(self, max_capital_uah: float, min_trade_uah: float, min_spread_pct: float,
                 safety_buffer_pct: float = 0.0):
        self.max_capital = Decimal(str(max_capital_uah))
        self.min_trade = Decimal(str(min_trade_uah))
        self.min_spread = Decimal(str(min_spread_pct))
        self.safety_buffer = Decimal(str(safety_buffer_pct))

    def match(self, buy_grouped: dict[str, list[Order]], sell_grouped: dict[str, list[Order]]) -> list[dict]:
        best_opportunities = []

        for buy_bank, buy_orders in buy_grouped.items():
            for sell_bank, sell_orders in sell_grouped.items():

                sorted_buys = sorted(buy_orders, key=lambda o: o.price)[:3]
                sorted_sells = sorted(sell_orders, key=lambda o: o.price, reverse=True)[:3]

                for buy in sorted_buys:
                    for sell in sorted_sells:
                        if sell.price <= buy.price:
                            continue

                        # ✅ Калькулятор тут — для кожної конкретної пари
                        calculator = get_calculator(buy_bank, sell_bank, buy.exchange, sell.exchange)

                        max_buy_fiat = min(self.max_capital, buy.max_limit, buy.available_amount * buy.price)

                        if max_buy_fiat < self.min_trade or max_buy_fiat < buy.min_limit:
                            continue

                        usdt_bought = max_buy_fiat / buy.price
                        usdt_to_sell = min(usdt_bought, sell.max_limit / sell.price, sell.available_amount)

                        if usdt_to_sell <= Decimal("0"):
                            continue

                        actual_buy_fiat = usdt_to_sell * buy.price
                        actual_sell_fiat = usdt_to_sell * sell.price

                        if actual_sell_fiat < sell.min_limit:
                            continue

                        gross_profit = actual_sell_fiat - actual_buy_fiat
                        gross_spread_pct = (gross_profit / actual_buy_fiat) * Decimal("100.0")

                        _, total_fee, fee_details = calculator.calculate_net(actual_buy_fiat)

                        net_profit = gross_profit - total_fee
                        net_spread_pct = (net_profit / actual_buy_fiat) * Decimal("100.0")

                        if net_spread_pct >= (self.min_spread + self.safety_buffer):
                            best_opportunities.append({
                                "buy_order": buy,
                                "sell_order": sell,
                                "buy_bank": buy_bank,
                                "sell_bank": sell_bank,
                                "actual_entry_uah": float(actual_buy_fiat),
                                "gross_spread_pct": float(gross_spread_pct),
                                "net_profit": float(net_profit),
                                "net_spread_pct": float(net_spread_pct),
                                "total_fee": float(total_fee)
                            })

        best_opportunities.sort(key=lambda x: x["net_spread_pct"], reverse=True)
        return best_opportunities