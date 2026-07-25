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
from typing import Optional, List

from exchanges.base import Order

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
        
    @staticmethod
    def analyze_sell_depth(
        buy_price: float,
        amount_usdt: float,
        sell_orders: List[Order],
        min_margin: float = 0.003,
        speed: str = "FAST",
        network_fee: float = 0.0,
    ) -> dict:
        """
        Аналізує глибину стакану для продажу з урахуванням об'єму та швидкості.
        
        :param buy_price: Ціна закупівлі
        :param amount_usdt: Обсяг на продаж (USDT)
        :param sell_orders: Стакан конкурентів (які ТАКОЖ продають USDT, side=0)
        :param min_margin: Мінімальна допустима маржа (щоб не піти в мінус)
        :param speed: "FAST" (враховуємо об'єм конкурентів, встаємо перед великим "стінкою") або "ANY" (просто найкраща ціна)
        """
        # 1. Базові обчислення
        fee_per_usdt = network_fee / max(amount_usdt, 1)
        absolute_min_sell = buy_price * (1 + min_margin) + fee_per_usdt
        
        if not sell_orders:
            # Немає конкурентів — ставимо ціну з маржею X (наприклад 1.5%)
            recommended = absolute_min_sell * 1.015
            return {
                "recommended_price": round(recommended, 4),
                "absolute_min_sell": round(absolute_min_sell, 4),
                "competitor_price": 0,
                "reason": "Стакан порожній. Встановлено дефолтну націнку."
            }

        # 2. Сортуємо стакан — нам потрібні найдешевші продавці (ми з ними конкуруємо)
        sorted_orders = sorted(sell_orders, key=lambda o: float(o.price))
        top_price = float(sorted_orders[0].price)
        
        # 3. Аналіз залежно від швидкості
        if speed == "ANY":
            # Якщо швидкість не важлива — ми просто перебиваємо топ-1 на 1 копійку, 
            # або хоча б стаємо на мінімально можливу ціну, якщо топ-1 занадто дешевий
            recommended = top_price - 0.0001
            reason = "Перебито топ-1 ціну (швидкість неважлива)"
        else:
            # Швидкість = FAST! Аналізуємо "стінки" ліквідності
            # Шукаємо першу ціну, перед якою НАКОПИЧЕНО достатньо об'єму (наприклад > нашого)
            accumulated_volume = 0.0
            wall_price = top_price
            
            for o in sorted_orders:
                wall_price = float(o.price)
                o_vol = float(o.max_limit) / wall_price if wall_price > 0 else 0
                accumulated_volume += o_vol
                
                # Якщо об'єми конкурентів перед нами ВЖЕ вдвічі більші за нашу суму —
                # то далі ставати немає сенсу, ми ніколи не продамо
                if accumulated_volume >= amount_usdt * 1.5:
                    break
                    
            # 🚀 Ми хочемо встати БЕЗПОСЕРЕДНЬО ПЕРЕД цією "стінкою"
            recommended = wall_price - 0.0001
            reason = f"Оптимальна ціна перед стінкою ліквідності ({accumulated_volume:.0f} USDT)"
            
        # 4. Перевірка Safety Breaker (Чи не продаємо ми в мінус?)
        if recommended < absolute_min_sell:
            recommended = absolute_min_sell
            reason += " (Спрацював Market Stop-Loss: ціну скориговано до мін. маржі)"
            
        # 5. Розрахунок прибутку
        profit = (amount_usdt - network_fee) * recommended - (amount_usdt * buy_price)
        
        return {
            "recommended_price": round(recommended, 4),
            "absolute_min_sell": round(absolute_min_sell, 4),
            "competitor_price": top_price,
            "reason": reason,
            "estimated_profit": round(profit, 2)
        }

