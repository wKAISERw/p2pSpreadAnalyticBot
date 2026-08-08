import { UserSettings } from '../types';

/**
 * Стартові локальні налаштування САЙТУ — поріг показу, звук, тема, ціль.
 *
 * Це єдиний мок, який лишився: stats, opportunities, logs, blacklist і
 * глобальні налаштування приходять із реального API, і підміна їх
 * фальшивими даними лише приховувала обірваний зв'язок з бекендом.
 *
 * Тут навмисно немає нічого, що вирішує бот. Раніше тут жив ще й блок
 * autoTrade з `allowedExchanges: ['Bybit','OKX']`: він не долітав до бота
 * взагалі, зате мовчки фільтрував дашборд — новий користувач не бачив
 * жодного спреду з Binance, MEXC, Wallet і BingX.
 */
export const mockUserSettings: UserSettings = {
  minSpread: 0.5,
  apiKeys: {},
  goalCapital: 50000,
  soundEnabled: true,
  soundVolume: 0.5,
  accentColor: 'emerald',
};
