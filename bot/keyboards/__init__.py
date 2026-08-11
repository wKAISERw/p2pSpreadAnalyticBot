"""
bot/keyboards/__init__.py
Ре-експортує всі клавіатури та глобальні константи — backward-compatible.
"""
from bot.keyboards.common import (
    back_to_main_kb, back_to_settings_kb, back_to_keys_kb,
    back_to_stats_kb, back_to_status_kb, EXCHANGE_ICONS, SCANNER_MODE_LABELS,
    back_to_monitoring_kb, back_to_system_kb, back_to_exchanges_kb,
    back_to_balance_kb, back_to_sessions_kb, back_to_filters_kb
)
from bot.keyboards.menu import main_menu_kb, system_menu_kb
from bot.keyboards.risk import (
    risk_main_kb, risk_profile_kb, risk_categories_kb,
    risk_group_kb, risk_signal_kb, risk_custom_kb, risk_back_kb,
)
from bot.keyboards.exchanges import (
    keys_menu_kb, exchange_connect_kb, exchange_down_kb,
    exchange_cooldown_kb, exchanges_status_kb, exchange_toggle_kb,
    create_ad_exchange_kb, create_ad_side_kb, create_ad_confirm_kb, create_ad_banks_kb,
    exchanges_menu_kb
)
from bot.keyboards.filters import (
    settings_menu_kb, display_settings_kb, global_settings_kb,
    banks_selection_kb, scanner_mode_kb, price_range_kb,
    filters_menu_kb
)
from bot.keyboards.cards import (
    card_display_settings_kb, cards_dashboard_kb, card_banks_kb,
    card_is_own_kb, card_details_kb, card_category_kb, report_period_kb,
    bank_limits_bank_kb, bank_limits_fields_kb, card_limits_fields_kb,
    LIMIT_FIELD_LABELS
)
from bot.keyboards.monitoring import (
    stats_overview_kb, stats_source_kb, stats_metrics_kb, monitoring_menu_kb,
    stats_period_kb, stats_mode_kb, stats_daily_with_details_kb
)

__all__ = [
    "back_to_main_kb",
    "back_to_settings_kb",
    "back_to_keys_kb",
    "back_to_stats_kb",
    "back_to_status_kb",
    "EXCHANGE_ICONS",          # 🚀 ДОДАНО ДЛЯ СУМІСНОСТІ
    "SCANNER_MODE_LABELS",      # 🚀 ДОДАНО ДЛЯ СУМІСНОСТІ
    "main_menu_kb",
    "keys_menu_kb",
    "exchange_connect_kb",
    "exchange_down_kb",
    "exchange_cooldown_kb",
    "exchanges_status_kb",
    "exchange_toggle_kb",
    "create_ad_exchange_kb",
    "create_ad_side_kb",
    "create_ad_confirm_kb",
    "create_ad_banks_kb",
    "settings_menu_kb",
    "display_settings_kb",
    "global_settings_kb",
    "banks_selection_kb",
    "scanner_mode_kb",
    "price_range_kb",
    "card_display_settings_kb",
    "cards_dashboard_kb",
    "card_banks_kb",
    "card_is_own_kb",
    "card_details_kb",
    "card_category_kb",
    "report_period_kb",
    "bank_limits_bank_kb",
    "bank_limits_fields_kb",
    "card_limits_fields_kb",
    "LIMIT_FIELD_LABELS",
    "stats_overview_kb",
    "stats_source_kb",
    "stats_metrics_kb",
    "stats_period_kb",
    "stats_mode_kb",
    "stats_daily_with_details_kb",
    "monitoring_menu_kb",
    "exchanges_menu_kb",
    "filters_menu_kb",
    "system_menu_kb",
    "back_to_monitoring_kb",
    "back_to_system_kb",
    "back_to_exchanges_kb",
    "back_to_balance_kb",
    "back_to_sessions_kb",
    "back_to_filters_kb",
]