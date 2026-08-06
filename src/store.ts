import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { UserSettings, GlobalSettings, AuthSession, LinkedIdentity } from './types';
import { mockUserSettings } from './data/mock';
import type { ConnectionState } from './services/api';
import { setSessionToken } from './services/api';

export type TradingMode = 'taker' | 'maker';

interface AppState {
  /**
   * Підтверджена особа. null = не увійшли. telegramId сюди потрапляє лише
   * з підписаного бекендом токена, а не з поля вводу — саме тому персональні
   * розділи більше не питають ID руками.
   */
  auth: Omit<AuthSession, 'token'> | null;
  setAuth: (session: AuthSession | null) => void;
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

  tradingMode: TradingMode;
  excludedExchanges: string[];

  setUserSettings: (settings: UserSettings) => void;
  setGlobalSettings: (settings: GlobalSettings) => void;
  patchGlobalSettings: (patch: Partial<GlobalSettings>) => void;

  setIsFocusMode: (isFocusMode: boolean) => void;
  setConnection: (connection: ConnectionState) => void;

  setTradingMode: (mode: TradingMode) => void;
  setExcludedExchanges: (exchanges: string[]) => void;
  toggleExcludedExchange: (exchange: string) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      auth: null,
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
      setIdentities: (identities) =>
        set((state) => ({ auth: state.auth ? { ...state.auth, identities } : null })),

      userSettings: mockUserSettings,
      globalSettings: {},
      globalSettingsLoaded: false,
      isFocusMode: false,
      connection: 'connecting',

      tradingMode: 'taker',
      excludedExchanges: [],

      setUserSettings: (settings) => set({ userSettings: settings }),
      setGlobalSettings: (settings) =>
        set({ globalSettings: settings, globalSettingsLoaded: true }),
      patchGlobalSettings: (patch) =>
        set((state) => ({ globalSettings: { ...state.globalSettings, ...patch } })),
      setIsFocusMode: (isFocusMode) => set({ isFocusMode }),
      setConnection: (connection) => set({ connection }),

      setTradingMode: (mode) => set({ tradingMode: mode }),
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
        tradingMode: state.tradingMode,
        excludedExchanges: state.excludedExchanges,
      }),
      // partialize впливає лише на запис. У браузерах, які вже ходили на
      // стару версію, у localStorage лежать globalSettings з мок-значеннями
      // та isConnected — без міграції вони пережили б регідратацію і знову
      // стали б джерелом «налаштувань з голови».
      version: 2,
      migrate: (persisted: any) => {
        if (!persisted) return persisted;
        const { globalSettings, isConnected, ...rest } = persisted;
        return rest;
      },
    }
  )
);
