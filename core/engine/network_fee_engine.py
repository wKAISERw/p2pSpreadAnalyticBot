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
# Довідник комісій мереж (USDT).
# ВАЖЛИВО: кожна мережа має лише ОДИН канонічний ключ.
# Аліаси (BSC, ARBITRUM тощо) перетворюються функцією _normalize_network()
# і ніколи не потрапляють у EXCHANGE_NETWORKS як окремі записи.
# ──────────────────────────────────────────────────────────────────────────────
NETWORK_FEES: dict[str, float] = {
    # Ультра-дешеві
    "SOL":      0.01,
    "TON":      0.01,
    # Дешеві Layer-2 та альтернативні
    "BEP20":    0.10,   # BSC — канонічна назва
    "ARB":      0.10,   # Arbitrum — канонічна назва
    "POLYGON":  0.10,   # MATIC — канонічна назва
    "OP":       0.10,   # Optimism — канонічна назва
    "AVAX":     0.10,
    "CELO":     0.10,
    # Середні
    "TRC20":    1.00,   # Tron — канонічна назва
    # Дорогі
    "ERC20":    15.00,  # Known limitation: хардкод до Фази 2+
}

# ──────────────────────────────────────────────────────────────────────────────
# ФІКС #10: Таблиця нормалізації аліасів → канонічна назва.
# get_fee("BSC") тепер коректно поверне 0.10, а не 999.0.
# ──────────────────────────────────────────────────────────────────────────────
_ALIASES: dict[str, str] = {
    "BSC":       "BEP20",
    "ARBITRUM":  "ARB",
    "MATIC":     "POLYGON",
    "OPTIMISM":  "OP",
    "TRON":      "TRC20",
    "ETH":       "ERC20",
    "ETHEREUM":  "ERC20",
    "SOLANA":    "SOL",
    "TONCHAIN":  "TON",
}

def _normalize_network(name: str) -> str:
    """Перетворює аліас мережі в канонічну назву. Регістр нечутливий."""
    upper = name.upper()
    return _ALIASES.get(upper, upper)


# ──────────────────────────────────────────────────────────────────────────────
# Підтримувані мережі для кожної біржі (USDT withdrawal).
# Використовуємо лише КАНОНІЧНІ назви — без дублів BSC/BEP20 тощо.
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
        """
        Повертає комісію за мережею з нормалізацією аліасів та fallback 999.0.
        ФІКС #10: get_fee("BSC") тепер повертає 0.10 замість 999.0.
        """
        canonical = _normalize_network(network)
        return NETWORK_FEES.get(canonical, 999.0)

    @staticmethod
    def format_for_alert(source_exchange: str, dest_exchange: str) -> str:
        """
        Блок D: Форматований блок мереж для Telegram-алерту.

        Приклад виводу:
            🏆 Мережа: TON (0.01 USDT)
            💱 Інші мережі:
            ├ SOL — 0.01 USDT
            ├ BEP20 — 0.10 USDT
            ├ TRC20 — 1.00 USDT
            └ ERC20 — 15.00 USDT
        """
        if source_exchange == dest_exchange:
            return "🏆 Мережа: INTRA (0.00 USDT) — внутрішній переказ"

        all_options = NetworkFeeEngine.get_all_options(source_exchange, dest_exchange)
        if not all_options:
            return "⚠️ Немає спільних мереж для переказу"

        best_net, best_fee = all_options[0]
        lines = [f"🏆 Мережа: {best_net} ({best_fee:.2f} USDT)"]

        others = all_options[1:]
        if others:
            lines.append("")
            lines.append("💱 Інші мережі:")
            for i, (net, fee) in enumerate(others):
                prefix = "└" if i == len(others) - 1 else "├"
                lines.append(f"{prefix} {net} — {fee:.2f} USDT")

        return "\n".join(lines)

