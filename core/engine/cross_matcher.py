from decimal import Decimal, getcontext
import logging
from exchanges.base import Order
from core.utils.fees import get_calculator

getcontext().prec = 28
logger = logging.getLogger("CrossMatcher")


class CrossMatchingEngine:
    def __init__(
        self,
        max_capital_uah: float,
        min_trade_uah: float,
        min_spread_pct: float,
        safety_buffer_pct: float = 0.0,
    ):
        self.max_capital = Decimal(str(max_capital_uah))
        self.min_trade = Decimal(str(min_trade_uah))
        self.min_spread = Decimal(str(min_spread_pct))
        self.safety_buffer = Decimal(str(safety_buffer_pct))

    @property
    def max_capital_uah(self) -> float:
        return float(self.max_capital)

    @max_capital_uah.setter
    def max_capital_uah(self, value: float) -> None:
        """scanner.py оновлює через цей setter на кожному циклі."""
        self.max_capital = Decimal(str(value))

    @property
    def min_spread_pct(self) -> float:
        return float(self.min_spread)

    @min_spread_pct.setter
    def min_spread_pct(self, value: float) -> None:
        """scanner.py оновлює через цей setter на кожному циклі."""
        self.min_spread = Decimal(str(value))

    def match(
        self,
        buy_grouped: dict[str, list[Order]],
        sell_grouped: dict[str, list[Order]],
    ) -> list[dict]:
        best_opportunities = []

        for buy_bank, buy_orders in buy_grouped.items():
            for sell_bank, sell_orders in sell_grouped.items():
                sorted_buys = sorted(buy_orders, key=lambda o: o.price)[:5]
                sorted_sells = sorted(sell_orders, key=lambda o: o.price, reverse=True)[:5]

                for buy in sorted_buys:
                    for sell in sorted_sells:
                        if sell.price <= buy.price:
                            continue

                        calculator = get_calculator(
                            buy_bank,
                            sell_bank,
                            buy.exchange,
                            sell.exchange,
                        )

                        max_buy_fiat = min(
                            self.max_capital,
                            buy.max_limit,
                            buy.available_amount * buy.price,
                        )

                        if max_buy_fiat < self.min_trade or max_buy_fiat < buy.min_limit:
                            continue

                        usdt_bought = max_buy_fiat / buy.price
                        usdt_to_sell = min(
                            usdt_bought,
                            sell.max_limit / sell.price,
                            sell.available_amount,
                        )

                        if usdt_to_sell <= Decimal("0"):
                            continue

                        actual_buy_fiat = usdt_to_sell * buy.price
                        actual_sell_fiat = usdt_to_sell * sell.price

                        if actual_sell_fiat < sell.min_limit:
                            continue

                        gross_profit = actual_sell_fiat - actual_buy_fiat
                        gross_spread_pct = (
                            gross_profit / actual_buy_fiat
                        ) * Decimal("100.0")

                        _, total_fee, fee_details = calculator.calculate_net(
                            actual_buy_fiat,
                            usdt_price=buy.price,
                        )

                        net_profit = gross_profit - total_fee
                        net_spread_pct = (
                            net_profit / actual_buy_fiat
                        ) * Decimal("100.0")

                        if net_spread_pct >= (self.min_spread + self.safety_buffer):
                            route_type = (
                                "INTRA" if buy.exchange == sell.exchange else "CROSS"
                            )

                            best_opportunities.append(
                                {
                                    "buy_order": buy,
                                    "sell_order": sell,
                                    "buy_bank": buy_bank,
                                    "sell_bank": sell_bank,
                                    "route_type": route_type,
                                    "actual_entry_uah": float(actual_buy_fiat),
                                    "gross_spread_pct": float(gross_spread_pct),
                                    "net_profit": float(net_profit),
                                    "net_spread_pct": float(net_spread_pct),
                                    "total_fee": float(total_fee),
                                    "fee_details": fee_details,
                                }
                            )

        best_opportunities.sort(key=lambda x: x["net_spread_pct"], reverse=True)
        return best_opportunities

    # ─── Grouping ─────────────────────────────────────────────────────────────

    def group(
        self,
        opportunities: list[dict],
        bank_names: dict[str, str],
    ) -> list[dict]:
        """
        Групує сирі можливості за унікальним маршрутом (мерчант+ціна).
        Збирає всі варіанти банківських пар в один алерт.
        bank_names: {internal_code: human_name}

        Перенесено з scanner.py._group_opportunities()
        """
        grouped: dict[str, dict] = {}

        for opp in opportunities:
            key = self._merge_key(opp)
            item = grouped.setdefault(key, {"base": opp.copy(), "route_pairs": set()})
            item["route_pairs"].add((opp["buy_bank"], opp["sell_bank"]))
            if float(opp["net_profit"]) > float(item["base"]["net_profit"]):
                item["base"] = opp.copy()

        merged: list[dict] = []
        for item in grouped.values():
            base = item["base"]
            buy_o = base["buy_order"]
            sell_o = base["sell_order"]

            buy_all = self._banks_sorted(getattr(buy_o, "bank_codes", []), bank_names)
            sell_all = self._banks_sorted(getattr(sell_o, "bank_codes", []), bank_names)

            base["buy_banks_all"]  = buy_all
            base["sell_banks_all"] = sell_all
            base["buy_banks_fit"]  = [b for b in buy_all if b in bank_names]
            base["sell_banks_fit"] = [b for b in sell_all if b in bank_names]
            base["route_variants"] = [
                f"{bank_names.get(b, b)} → {bank_names.get(s, s)}"
                for b, s in sorted(
                    item["route_pairs"],
                    key=lambda p: (bank_names.get(p[0], p[0]), bank_names.get(p[1], p[1])),
                )
            ]
            merged.append(base)

        merged.sort(
            key=lambda x: (float(x["net_profit"]), float(x["net_spread_pct"])),
            reverse=True,
        )
        return merged

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_key(opp: dict) -> str:
        b = opp["buy_order"]
        s = opp["sell_order"]
        return (
            f"{opp.get('route_type','UNKNOWN')}|"
            f"{b.exchange}|{b.merchant_id}|{b.price}|{b.min_limit}|{b.max_limit}|{b.link}|"
            f"{s.exchange}|{s.merchant_id}|{s.price}|{s.min_limit}|{s.max_limit}|{s.link}|"
            f"{round(float(opp['actual_entry_uah']), 2)}"
        )

    @staticmethod
    def _banks_sorted(codes: list[str] | None, bank_names: dict[str, str]) -> list[str]:
        uniq = {c for c in (codes or []) if c}
        return sorted(uniq, key=lambda c: bank_names.get(c, c))

    @staticmethod
    def order_fingerprint(order, bank_code: str) -> str:
        """Унікальний відбиток ордера для дедуплікації."""
        return (
            f"{order.exchange}|{order.merchant_id}|{bank_code}|"
            f"{order.price}|{order.min_limit}|{order.max_limit}|{order.link}"
        )