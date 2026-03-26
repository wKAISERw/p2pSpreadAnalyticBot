import { create } from 'zustand';
import { persist } from 'zustand/middleware'; // ДОДАНО
import { UserSettings, GlobalSettings, SystemStats, ArbitrageOpportunity, AutoTradeLog, BlacklistEntry } from './types';
import { mockUserSettings, mockGlobalSettings } from './data/mock';



interface AppState {
  userSettings: UserSettings;
  globalSettings: GlobalSettings;
  isAdmin: boolean;
  isFocusMode: boolean;
  isConnected: boolean | null;
  
  setUserSettings: (settings: UserSettings) => void;
  setGlobalSettings: (settings: GlobalSettings) => void;
  setIsAdmin: (isAdmin: boolean) => void;
  setIsFocusMode: (isFocusMode: boolean) => void;
  setIsConnected: (isConnected: boolean | null) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      userSettings: mockUserSettings,
      globalSettings: mockGlobalSettings,
      isAdmin: false,
      isFocusMode: false,
      isConnected: null,

      setUserSettings: (settings) => set({ userSettings: settings }),
      setGlobalSettings: (settings) => set({ globalSettings: settings }),
      setIsAdmin: (isAdmin) => set({ isAdmin }),
      setIsFocusMode: (isFocusMode) => set({ isFocusMode }),
      setIsConnected: (isConnected) => set({ isConnected }),
    }),
    {
      name: 'arbix-storage', // Ім'я ключа в localStorage браузера
    }
  )
);
