# core/engine/price_advisor.py
"""
PriceAdvisor — калькулятор/порадник ціни для мейкер-режимів.

Для MAKER_SELL:
  - Вводиш ціну купівлі → отримуєш мінімальну рентабельну ціну продажу.
  - Формула: min_sell = buy_price × (1 + min_margin) + (network_fee / amount)

Для MAKER_BUY:
  - Аналізує стакан sell-ордерів → рекомендує оптимальну ціну купівлі.
  - Логіка: зараз топ-продавець по X, щоб купити і продати з маржею Y%
    рекомендуємо ≤ (X - margin).
"""
from __future__ import annotations
import logging
from typing import Optional

from core.engine.network_fee_engine import NetworkFeeEngine

logger = logging.getLogger("PriceAdvisor")


class PriceAdvisor:
    """Порадник ціни для Maker-оголошень."""

    @staticmethod
    def suggest_sell_price(
        buy_price: float,
        amount_usdt: float,
        buy_exchange: str = "",
        sell_exchange: str = "",
        network_fee: float = 0.0,
        min_margin: float = 0.003,
    ) -> dict:
        """
        Розраховує мінімальну ціну продажу для прибутковості.

        :param buy_price: ціна по якій купив USDT (UAH)
        :param amount_usdt: кількість USDT
        :param buy_exchange: біржа купівлі (для розрахунку fee)
        :param sell_exchange: біржа продажу
        :param network_fee: вже відома мережева комісія (USDT), або 0 для авторозрахунку
        :param min_margin: мінімальна маржа (0.003 = 0.3%)
        :return: dict з порадами
        """
        # Авто-розрахунок мережевої комісії якщо не задана
        if network_fee <= 0 and buy_exchange and sell_exchange:
            if buy_exchange != sell_exchange:
                _, network_fee = NetworkFeeEngine.get_optimal_network(
                    buy_exchange, sell_exchange
                )
                if network_fee >= 999.0:
                    network_fee = 1.0  # fallback

        fee_per_usdt = network_fee / max(amount_usdt, 1)
        min_sell_price = buy_price * (1 + min_margin) + fee_per_usdt

        # Розрахунок профіту при різних цінах
        sell_amount = amount_usdt - network_fee
        profit_at_min = sell_amount * min_sell_price - amount_usdt * buy_price
        profit_pct_at_min = (profit_at_min / (amount_usdt * buy_price)) * 100 if buy_price > 0 else 0

        return {
            "min_sell_price": round(min_sell_price, 4),
            "buy_price": buy_price,
            "network_fee": network_fee,
            "amount_usdt": amount_usdt,
            "sell_amount_after_fee": round(sell_amount, 4),
            "min_margin_pct": min_margin * 100,
            "profit_at_min_uah": round(profit_at_min, 2),
            "profit_at_min_pct": round(profit_pct_at_min, 2),
        }

    @staticmethod
    def suggest_buy_price(
        sell_book_top: float,
        sell_exchange: str = "",
        buy_exchange: str = "",
        amount_usdt: float = 500.0,
        target_margin: float = 0.005,
    ) -> dict:
        """
        Рекомендує оптимальну ціну купівлі на основі стакану продажу.

        Логіка: якщо найвигідніший продавець пропонує по sell_book_top,
        то для заробітку target_margin% потрібно купити по ціні
        ≤ sell_book_top / (1 + target_margin) - fee/amount.

        :param sell_book_top: найкраща ціна в стакані продажу (UAH/USDT)
        :param sell_exchange: де будемо продавати
        :param buy_exchange: де будемо купувати
        :param amount_usdt: орієнтовний об'єм
        :param target_margin: бажана маржа (0.005 = 0.5%)
        :return: dict з порадами
        """
        network_fee = 0.0
        if buy_exchange and sell_exchange and buy_exchange != sell_exchange:
            _, network_fee = NetworkFeeEngine.get_optimal_network(
                buy_exchange, sell_exchange
            )
            if network_fee >= 999.0:
                network_fee = 1.0

        fee_per_usdt = network_fee / max(amount_usdt, 1)
        max_buy_price = (sell_book_top - fee_per_usdt) / (1 + target_margin)

        profit_estimate = (amount_usdt - network_fee) * sell_book_top - amount_usdt * max_buy_price

        return {
            "max_buy_price": round(max_buy_price, 4),
            "sell_book_top": sell_book_top,
            "network_fee": network_fee,
            "target_margin_pct": target_margin * 100,
            "estimated_profit_uah": round(profit_estimate, 2),
        }

    @staticmethod
    def format_sell_suggestion(advice: dict) -> str:
        """Форматує пораду для Telegram."""
        return (
            f"💡 <b>Порада (Продаж)</b>\n"
            f"├ Ціна купівлі: <code>{advice['buy_price']:.2f}</code> ₴\n"
            f"├ Мережева комісія: <code>{advice['network_fee']:.2f}</code> USDT\n"
            f"├ USDT після fee: <code>{advice['sell_amount_after_fee']:.2f}</code>\n"
            f"├ Мін. маржа: <code>{advice['min_margin_pct']:.1f}%</code>\n"
            f"├ <b>Мін. ціна продажу: <code>{advice['min_sell_price']:.4f}</code> ₴</b>\n"
            f"└ Профіт при мін. ціні: <code>+{advice['profit_at_min_uah']:.2f}</code> ₴ "
            f"({advice['profit_at_min_pct']:.2f}%)"
        )

    @staticmethod
    def format_buy_suggestion(advice: dict) -> str:
        """Форматує пораду для Telegram."""
        return (
            f"💡 <b>Порада (Купівля)</b>\n"
            f"├ Топ продавець: <code>{advice['sell_book_top']:.2f}</code> ₴\n"
            f"├ Мережева комісія: <code>{advice['network_fee']:.2f}</code> USDT\n"
            f"├ Бажана маржа: <code>{advice['target_margin_pct']:.1f}%</code>\n"
            f"├ <b>Макс. ціна купівлі: <code>{advice['max_buy_price']:.4f}</code> ₴</b>\n"
            f"└ Очікуваний профіт: <code>+{advice['estimated_profit_uah']:.2f}</code> ₴"
        )

