import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { UserSettings, GlobalSettings, AuthSession, LinkedIdentity } from './types';
import { mockUserSettings } from './data/mock';
import type { ConnectionState } from './services/api';
import { setSessionToken } from './services/api';

/**
 * Що показує дашборд.
 *
 * Це вибір ВИДУ, а не режиму бота. Раніше тут було бінарне taker|maker, і
 * «taker» означав список спред-зв'язок — тобто назва суперечила тому, що
 * малювалось. Тепер види названі тим, чим вони є, і тейкер-сторони можна
 * дивитись поодинці або разом.
 *
 * scanner_mode (те, що бот шле в Telegram) живе окремо, у базі бота:
 * дивитись на сайті бік купівлі, поки бот працює на спред, — нормально.
 */
export type DashboardView = 'spread' | 'buy' | 'sell' | 'both' | 'maker';

interface AppState {
  /**
   * Підтверджена особа. null = не увійшли. telegramId сюди потрапляє лише
   * з підписаного бекендом токена, а не з поля вводу — саме тому персональні
   * розділи більше не питають ID руками.
   */
  auth: Omit<AuthSession, 'token'> | null;
  /**
   * Чи вже перевірено токен із localStorage на бекенді.
   *
   * Потрібен, щоб сторінки не приймали рішень до відповіді /auth/me:
   * без нього AppShell бачив auth === null і встигав відкинути на
   * /login ще до того, як сесія відновиться.
   */
  authRestored: boolean;
  setAuth: (session: AuthSession | null) => void;
  setAuthRestored: (restored: boolean) => void;
  setIdentities: (identities: LinkedIdentity[]) => void;

  userSettings: UserSettings;
  /**
   * Глобальні налаштування сканера. Порожній об'єкт до першого
   * GET /settings/global — раніше тут лежали мок-значення, і адмінка
   * пушила їх на сервер при першій же зміні будь-якого поля.
   */
  globalSettings: GlobalSettings;
  globalSettingsLoaded: boolean;
  isFocusMode: boolean;
  connection: ConnectionState;

  dashboardView: DashboardView;
  excludedExchanges: string[];

  setUserSettings: (settings: UserSettings) => void;
  setGlobalSettings: (settings: GlobalSettings) => void;
  patchGlobalSettings: (patch: Partial<GlobalSettings>) => void;

  setIsFocusMode: (isFocusMode: boolean) => void;
  setConnection: (connection: ConnectionState) => void;

  setDashboardView: (view: DashboardView) => void;
  setExcludedExchanges: (exchanges: string[]) => void;
  toggleExcludedExchange: (exchange: string) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      auth: null,
      authRestored: false,
      setAuth: (session) => {
        // Токен живе в localStorage окремо: його підставляє інтерцептор
        // axios, а в сторі тримати секрет ні до чого.
        setSessionToken(session?.token ?? '');
        set({
          auth: session
            ? { telegramId: session.telegramId, isAdmin: session.isAdmin, identities: session.identities }
            : null,
        });
      },
      setAuthRestored: (authRestored) => set({ authRestored }),

      setIdentities: (identities) =>
        set((state) => ({ auth: state.auth ? { ...state.auth, identities } : null })),

      userSettings: mockUserSettings,
      globalSettings: {},
      globalSettingsLoaded: false,
      isFocusMode: false,
      connection: 'connecting',

      dashboardView: 'spread',
      excludedExchanges: [],

      setUserSettings: (settings) => set({ userSettings: settings }),
      setGlobalSettings: (settings) =>
        set({ globalSettings: settings, globalSettingsLoaded: true }),
      patchGlobalSettings: (patch) =>
        set((state) => ({ globalSettings: { ...state.globalSettings, ...patch } })),
      setIsFocusMode: (isFocusMode) => set({ isFocusMode }),
      setConnection: (connection) => set({ connection }),

      setDashboardView: (view) => set({ dashboardView: view }),
      setExcludedExchanges: (exchanges) => set({ excludedExchanges: exchanges }),
      toggleExcludedExchange: (exchange) =>
        set((state) => {
          const currentExcluded = state.excludedExchanges || [];
          return {
            excludedExchanges: currentExcluded.includes(exchange)
              ? currentExcluded.filter((e) => e !== exchange)
              : [...currentExcluded, exchange],
          };
        }),
    }),
    {
      name: 'arbix-storage',
      // Глобальні налаштування і стан зв'язку — завжди з сервера, не з кешу.
      // auth теж не персистимо: джерело правди — токен у localStorage,
      // який перевіряється через /auth/me на старті.
      partialize: (state) => ({
        userSettings: state.userSettings,
        isFocusMode: state.isFocusMode,
        dashboardView: state.dashboardView,
        excludedExchanges: state.excludedExchanges,
      }),
      // partialize впливає лише на запис. У браузерах, які вже ходили на
      // стару версію, у localStorage лежать globalSettings з мок-значеннями
      // та isConnected — без міграції вони пережили б регідратацію і знову
      // стали б джерелом «налаштувань з голови».
      version: 4,
      migrate: (persisted: any) => {
        if (!persisted) return persisted;
        const { globalSettings, isConnected, ...rest } = persisted;

        // v3: блок autoTrade прибрано. Він ніколи не долітав до бота —
        // виконанням угод керує trade_worker, HTTP-ендпоінта під нього
        // немає, — але його `allowedExchanges` мовчки фільтрував дашборд.
        // У браузерах, які вже сюди ходили, він лежить у localStorage, тож
        // викидаємо його явно.
        //
        // v4: туди ж поїхали maxCapital, minCapital, banks і syncPreferences.
        // Це були локальні копії того, що живе в базі бота; єдиним їхнім
        // читачем лишалась панель синхронізації, яка сама ж їх і заповнювала.
        for (const dead of [
          'autoTrade', 'maxCapital', 'minCapital', 'banks', 'syncPreferences',
          'autoSyncTelegram',
        ]) {
          if (rest?.userSettings && dead in rest.userSettings) {
            delete rest.userSettings[dead];
          }
        }

        return rest;
      },
    }
  )
);
