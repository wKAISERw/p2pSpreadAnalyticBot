import useSWR from 'swr';
import { api } from '../services/api';
import { ExchangeStatus } from '../types';

/**
 * Список бірж брався з жорстко вписаних масивів у трьох різних компонентах.
 * За цей час сканер відростив Wallet, BingX і CryptoBot — на дашборді їх
 * просто не існувало. Тепер джерело одне: GET /api/v1/exchanges.
 */

/** Використовується, поки запит не долетів або бекенд недоступний. */
export const EXCHANGE_FALLBACK = ['Binance', 'Bybit', 'OKX', 'MEXC'];

export function useExchanges() {
  const { data, error, isLoading } = useSWR<ExchangeStatus[]>(
    '/exchanges',
    () => api.getExchanges(),
    { refreshInterval: 15000, shouldRetryOnError: false }
  );

  return {
    exchanges: data ?? [],
    names: data?.length ? data.map((e) => e.name) : EXCHANGE_FALLBACK,
    isLoading,
    error,
  };
}
