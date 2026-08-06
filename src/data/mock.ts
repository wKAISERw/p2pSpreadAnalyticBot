import { UserSettings } from '../types';

/**
 * Стартові локальні налаштування користувача — до першого входу і до
 * синхронізації з ботом. Це єдиний мок, який лишився: решта (stats,
 * opportunities, logs, blacklist, global settings) тепер приходить
 * з реального API, і підміна їх фальшивими даними лише приховувала
 * обірваний зв'язок з бекендом.
 */
export const mockUserSettings: UserSettings = {
  minCapital: 5000,
  maxCapital: 15000,
  minSpread: 0.5,
  banks: ['43', '14'],
  merchantFilters: {
    minOrders: 50,
    minRate: 95.0,
  },
  autoTrade: {
    enabled: false,
    maxTradeAmount: 5000,
    minSpread: 0.8,
    allowedExchanges: ['Bybit', 'OKX'],
    maxRiskScore: 30,
    maxCapital: 15000,
  },
  apiKeys: {},
  goalCapital: 50000,
  soundEnabled: true,
  soundVolume: 0.5,
  accentColor: 'emerald',
};
