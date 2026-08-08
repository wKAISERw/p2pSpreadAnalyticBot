import useSWR from 'swr';
import { api } from '../services/api'; // Перевір, чи правильний шлях до api.ts
import { useAppStore } from '../store';

/**
 * Дані акаунта біржі з GET /accounts/{id}.
 *
 * Поля, крім балансів, nullable свідомо: бекенд уміє їх дістати лише для
 * частини бірж (Binance і OKX). Раніше замість null там лежали константи
 * «Verified» / «None» / 0 / 100000 — цифри, яких ніхто не питав у біржі.
 */
export interface ExchangeAccount {
  id: string;
  exchange: string;
  balanceUAH: number;
  balanceUSDT: number;
  kycLevel: string | null;
  merchantStatus: 'Active' | 'Pending' | 'None' | null;
  tradingVolume30d: number | null;
  volumeLimit: number | null;
  /** Заповнене, якщо біржа не відповіла: баланси тоді нульові несправжні. */
  error?: string;
}

export function useExchangeAccounts() {
  // Дістаємо ID користувача з Zustand-стора
  // Особа з підтвердженої сесії, а не з поля вводу в налаштуваннях.
  const telegramId = useAppStore((state) => state.auth?.telegramId);

  // Робимо запит ТІЛЬКИ якщо є telegramId
  const { data, error, isLoading, mutate } = useSWR<ExchangeAccount[]>(
    telegramId ? `/accounts/${telegramId}` : null,
    () => api.getAccounts(telegramId!),
    {
      refreshInterval: 30000, // Автоматично оновлювати баланси кожні 30 секунд
      revalidateOnFocus: true,
    }
  );

  return {
    data: data || [],
    isLoading,
    isError: error,
    refresh: mutate,
  };
}