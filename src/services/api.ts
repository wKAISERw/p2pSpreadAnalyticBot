/// <reference types="vite/client" />
import { mockStats, mockOpportunities, mockLogs, mockGlobalSettings, mockUserSettings, mockBlacklist } from '../data/mock';
import { SystemStats, ArbitrageOpportunity, AutoTradeLog, GlobalSettings, UserSettings, BlacklistEntry } from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

async function fetchWithFallback<T>(endpoint: string, fallbackData: T): Promise<T> {
  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.warn(`[API] Бекенд недоступний (${endpoint}). Використовуються тестові дані.`, error);
    return fallbackData;
  }
}

export const api = {
  getStats: () => fetchWithFallback<SystemStats>('/stats', mockStats),
  getOpportunities: () => fetchWithFallback<ArbitrageOpportunity[]>('/opportunities', mockOpportunities),
  getLogs: () => fetchWithFallback<AutoTradeLog[]>('/logs', mockLogs),
  getGlobalSettings: () => fetchWithFallback<GlobalSettings>('/settings/global', mockGlobalSettings),
  getBlacklist: () => fetchWithFallback<BlacklistEntry[]>('/blacklist', mockBlacklist),

  checkConnection: async (): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE_URL}/stats`);
      return response.ok;
    } catch (error) {
      return false;
    }
  },

  updateGlobalSettings: async (settings: GlobalSettings): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE_URL}/settings/global`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
      });
      return response.ok;
    } catch (error) {
      console.error('[API] Помилка збереження налаштувань:', error);
      return false;
    }
  },

  addToBlacklist: async (entry: Omit<BlacklistEntry, 'addedAt'>): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE_URL}/blacklist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry)
      });
      return response.ok;
    } catch (error) {
      console.error('[API] Помилка додавання в чорний список:', error);
      return false;
    }
  },

  removeFromBlacklist: async (merchantId: string, exchange: string): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE_URL}/blacklist/${exchange}/${merchantId}`, {
        method: 'DELETE'
      });
      return response.ok;
    } catch (error) {
      console.error('[API] Помилка видалення з чорного списку:', error);
      return false;
    }
  },

  syncTelegram: async (telegramId: string): Promise<any> => {
    try {
      const response = await fetch(`${API_BASE_URL}/telegram/sync/${telegramId}`);
      if (response.ok) return await response.json();
      throw new Error('Backend not ready');
    } catch (error) {
      console.warn('[API] Мок-синхронізація з Telegram ботом');
      // Mock response: overwrite with bot's settings, grant admin, and pull connected exchanges
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
      await fetch(`${API_BASE_URL}/user/settings`, { // Змінено на правильний ендпоінт з main.py
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Явно передаємо telegramUserId в тілі, бо бекенд чекає його через settings.get("telegramUserId")
        body: JSON.stringify({ ...settings, telegramUserId: telegramId })
      });
    } catch (error) {
      console.warn('[API] Мок-оновлення налаштувань у Telegram боті', error);
    }
  }
};
