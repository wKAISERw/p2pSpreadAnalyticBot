/// <reference types="vite/client" />
import { mockStats, mockOpportunities, mockLogs, mockGlobalSettings, mockUserSettings, mockBlacklist } from '../data/mock';
import { SystemStats, ArbitrageOpportunity, AutoTradeLog, GlobalSettings, UserSettings, BlacklistEntry } from '../types';/// <reference types="vite/client" />
// URL вашого бекенду. За замовчуванням localhost:8000 для локальної розробки (FastAPI)
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

/**
 * Універсальна функція для GET запитів.
 * Якщо бекенд недоступний, вона автоматично повертає mock-дані (заглушки),
 * щоб фронтенд не ламався і продовжував працювати візуально.
 */
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
  // Отримання даних
 getStats: () => fetchWithFallback<SystemStats>('/stats', mockStats),
  getOpportunities: () => fetchWithFallback<ArbitrageOpportunity[]>('/opportunities', mockOpportunities),
  getLogs: () => fetchWithFallback<AutoTradeLog[]>('/logs', mockLogs),
  getGlobalSettings: () => fetchWithFallback<GlobalSettings>('/settings/global', mockGlobalSettings),
  getUserSettings: () => fetchWithFallback<UserSettings>('/settings/user', mockUserSettings),
  getBlacklist: () => fetchWithFallback<BlacklistEntry[]>('/blacklist', mockBlacklist),

  // Перевірка статусу підключення
  checkConnection: async (): Promise<boolean> => {
    try {
      const response = await fetch(`${API_BASE_URL}/stats`);
      return response.ok;
    } catch (error) {
      return false;
    }
  },

  // Приклад POST запиту (відправка налаштувань на бекенд)
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

  // Приклад POST запиту (додавання в чорний список)
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
  }
};
