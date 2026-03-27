import { create } from 'zustand';
import { persist } from 'zustand/middleware'; // ДОДАНО
import { UserSettings, GlobalSettings, SystemStats, ArbitrageOpportunity, AutoTradeLog, BlacklistEntry } from './types';
import { mockUserSettings, mockGlobalSettings } from './data/mock';

export type TradingMode = 'taker' | 'maker';

interface AppState {
  userSettings: UserSettings;
  globalSettings: GlobalSettings;
  isAdmin: boolean;
  isFocusMode: boolean;
  isConnected: boolean | null;
  
  // New: Trading mode (Taker / Maker)
  tradingMode: TradingMode;

  // New: Excluded exchanges (for "scan all EXCEPT these")
  excludedExchanges: string[];

  setUserSettings: (settings: UserSettings) => void;
  setGlobalSettings: (settings: GlobalSettings) => void;
  setIsAdmin: (isAdmin: boolean) => void;
  setIsFocusMode: (isFocusMode: boolean) => void;
  setIsConnected: (isConnected: boolean | null) => void;

  // New actions
  setTradingMode: (mode: TradingMode) => void;
  setExcludedExchanges: (exchanges: string[]) => void;
  toggleExcludedExchange: (exchange: string) => void;
}



export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      userSettings: mockUserSettings,
      globalSettings: mockGlobalSettings,
      isAdmin: false,
      isFocusMode: false,
      isConnected: null,

      // New defaults
      tradingMode: 'taker',
      excludedExchanges: [],

      setUserSettings: (settings) => set({ userSettings: settings }),
      setGlobalSettings: (settings) => set({ globalSettings: settings }),
      setIsAdmin: (isAdmin) => set({ isAdmin }),
      setIsFocusMode: (isFocusMode) => set({ isFocusMode }),
      setIsConnected: (isConnected) => set({ isConnected }),

      // New actions
      setTradingMode: (mode) => set({ tradingMode: mode }),
      setExcludedExchanges: (exchanges) => set({ excludedExchanges: exchanges }),
      toggleExcludedExchange: (exchange) => set((state) => {
        // Захист від старого кешу, де excludedExchanges ще не існує (undefined)
        const currentExcluded = state.excludedExchanges || [];

        return {
          excludedExchanges: currentExcluded.includes(exchange)
            ? currentExcluded.filter(e => e !== exchange)
            : [...currentExcluded, exchange]
        };
      }),
    }),
    {
      name: 'arbix-storage',
    }
  )
);

