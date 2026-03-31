"""
NetworkFeeEngine — Блок 1: Розрахунок комісій мереж.

Автоматично обирає найдешевшу мережу для переказу USDT між біржами,
базуючись на перетині підтримуваних мереж.

Формула профіту:
    profit = (usdt_bought - optimal_network_fee) * sell_rate - deal_uah

Джерела даних комісій:
    - NETWORK_FEES: хардкод відомих мереж (стабільні значення)
    - Для ERC20 ~15 USDT (до Фази 2 — потім брати з API)
"""

from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Довідник комісій мереж (USDT)
# Значення: типова комісія за вихідний переказ (withdrawal fee)
# ──────────────────────────────────────────────────────────────────────────────
NETWORK_FEES: dict[str, float] = {
    # Ультра-дешеві
    "SOL":           0.01,
    "TON":           0.01,
    # Дешеві Layer-2 та альтернативні
    "BEP20":         0.10,
    "BSC":           0.10,
    "ARB":           0.10,
    "ARBITRUM":      0.10,
    "POLYGON":       0.10,
    "MATIC":         0.10,
    "OP":            0.10,
    "OPTIMISM":      0.10,
    "AVAX":          0.10,
    "CELO":          0.10,
    # Середні
    "TRC20":         1.00,
    "TRON":          1.00,
    # Дорогі
    "ERC20":        15.00,   # Known limitation: хардкод до Фази 2+
    "ETH":          15.00,
}

# ──────────────────────────────────────────────────────────────────────────────
# Підтримувані мережі для кожної біржі (USDT withdrawal)
# ──────────────────────────────────────────────────────────────────────────────
EXCHANGE_NETWORKS: dict[str, list[str]] = {
    "Bybit":     ["TRC20", "ERC20", "BEP20", "SOL", "ARB", "POLYGON", "OP", "TON"],
    "Binance":   ["TRC20", "ERC20", "BEP20", "SOL", "ARB", "POLYGON", "OP", "TON"],
    "OKX":       ["TRC20", "ERC20", "BEP20", "SOL", "ARB", "TON"],
    "MEXC":      ["TRC20", "ERC20", "BEP20", "SOL"],
    "CryptoBot": ["TON", "TRC20"],
    "Wallet":    ["TON"],
}


class NetworkFeeEngine:
    """
    Блок 1: Двигун вибору оптимальної мережі та розрахунку комісій.
    """

    @staticmethod
    def get_optimal_network(
        source_exchange: str,
        dest_exchange: str,
    ) -> tuple[str, float]:
        """
        Знаходить найдешевшу спільну мережу між двома біржами.

        :returns: (network_name, fee_usdt)
                  Якщо мережа не знайдена — повертає ("UNKNOWN", 999.0)
        """
        if source_exchange == dest_exchange:
            return ("INTRA", 0.0)

        source_nets = set(EXCHANGE_NETWORKS.get(source_exchange, []))
        dest_nets   = set(EXCHANGE_NETWORKS.get(dest_exchange, []))
        common_nets = source_nets & dest_nets

        if not common_nets:
            return ("UNKNOWN", 999.0)

        # Сортуємо за ціною і беремо найдешевшу
        best = min(common_nets, key=lambda n: NETWORK_FEES.get(n, 999.0))
        return (best, NETWORK_FEES.get(best, 999.0))

    @staticmethod
    def get_all_options(
        source_exchange: str,
        dest_exchange: str,
    ) -> list[tuple[str, float]]:
        """
        Повертає всі спільні мережі відсортовані від найдешевшої.
        Використовується для відображення альтернатив у Telegram.
        """
        if source_exchange == dest_exchange:
            return [("INTRA", 0.0)]

        source_nets = set(EXCHANGE_NETWORKS.get(source_exchange, []))
        dest_nets   = set(EXCHANGE_NETWORKS.get(dest_exchange, []))
        common_nets = source_nets & dest_nets

        return sorted(
            [(n, NETWORK_FEES.get(n, 999.0)) for n in common_nets],
            key=lambda x: x[1]
        )

    @staticmethod
    def calc_profit(
        amount_usdt: float,
        buy_price: float,
        sell_price: float,
        network_fee: float,
    ) -> float:
        """
        Розраховує чистий профіт угоди.

        profit = (usdt_bought - fee) * sell_rate - cost_uah
        """
        cost_uah    = amount_usdt * buy_price
        revenue_uah = (amount_usdt - network_fee) * sell_price
        return revenue_uah - cost_uah

    @staticmethod
    def get_fee(network: str) -> float:
        """Повертає комісію за мережею з fallback 999.0."""
        return NETWORK_FEES.get(network.upper(), NETWORK_FEES.get(network, 999.0))
