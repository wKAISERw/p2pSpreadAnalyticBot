import useSWR from 'swr';
import { api } from '../services/api'; // Перевір, чи правильний шлях до api.ts
import { useAppStore } from '../store';

export interface ExchangeAccount {
  id: string;
  exchange: string;
  balanceUAH: number;
  balanceUSDT: number;
  kycLevel: string;
  merchantStatus: 'Active' | 'Pending' | 'None';
  tradingVolume30d: number;
  volumeLimit: number;
}

export function useExchangeAccounts() {
  // Дістаємо ID користувача з Zustand-стора
  const userSettings = useAppStore((state) => state.userSettings);
  const telegramId = userSettings?.telegramUserId;

  // Робимо запит ТІЛЬКИ якщо є telegramId
  const { data, error, isLoading, mutate } = useSWR<ExchangeAccount[]>(
    telegramId ? `/accounts/${telegramId}` : null,
    () => api.getAccounts(telegramId as string),
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