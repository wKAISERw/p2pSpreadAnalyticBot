/// <reference types="vite/client" />
import axios from 'axios';
import { SystemStats, ArbitrageOpportunity, AutoTradeLog, GlobalSettings, UserSettings, BlacklistEntry, ApiKeyConfig } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
});

apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error(`[API Error] ${error.config?.url}:`, error.message);
    return Promise.reject(error);
  }
);

export const api = {
  getStats: () => apiClient.get<any, SystemStats>('/stats'),
  getOpportunities: () => apiClient.get<any, ArbitrageOpportunity[]>('/opportunities'),
  getLogs: () => apiClient.get<any, AutoTradeLog[]>('/logs'),
  getGlobalSettings: () => apiClient.get<any, GlobalSettings>('/settings/global'),
  getBlacklist: () => apiClient.get<any, BlacklistEntry[]>('/blacklist'),

  saveCredentials: async (exchange: string, keys: ApiKeyConfig): Promise<boolean> => {
    try {
      await apiClient.post(`/credentials/${exchange.toLowerCase()}`, keys);
      return true;
    } catch (error) {
      return false;
    }
  },

  updateUserSettings: async (settings: UserSettings): Promise<boolean> => {
    try {
      await apiClient.post('/settings/user', settings);
      return true;
    } catch (error) {
      return false;
    }
  },

  checkConnection: async (): Promise<boolean> => {
    try {
      await apiClient.get('/stats');
      return true;
    } catch (error) {
      return false;
    }
  },

  updateGlobalSettings: async (settings: GlobalSettings): Promise<boolean> => {
    try {
      await apiClient.post('/settings/global', settings);
      return true;
    } catch (error) {
      return false;
    }
  },

  addToBlacklist: async (entry: Omit<BlacklistEntry, 'addedAt'>): Promise<boolean> => {
    try {
      await apiClient.post('/blacklist', entry);
      return true;
    } catch (error) {
      return false;
    }
  },

  removeFromBlacklist: async (merchantId: string, exchange: string): Promise<boolean> => {
    try {
      await apiClient.delete(`/blacklist/${exchange}/${merchantId}`);
      return true;
    } catch (error) {
      return false;
    }
  },

  syncTelegram: async (telegramId: string): Promise<any> => {
    try {
      return await apiClient.get(`/telegram/sync/${telegramId}`);
    } catch (error) {
      console.warn('[API] Мок-синхронізація з Telegram ботом');
      return {
        settings: {
          minCapital: 10000,
          maxCapital: 50000,
          minSpread: 0.8,
          banks: ['43', '14'],
        },
        keys: ['binance', 'bybit'],
        isAdmin: true
      };
    }
  },

  updateTelegramSettings: async (telegramId: string, settings: any): Promise<void> => {
    try {
      await apiClient.post('/user/settings', { ...settings, telegramUserId: telegramId });
    } catch (error) {
      console.warn('[API] Мок-оновлення налаштувань у Telegram боті', error);
    }
  }
};
