import useSWR from 'swr';
import { MakerOpportunity, generateMockMakerOpportunities } from '../types/maker';
import { useAppStore } from '../store';
import { useMemo } from 'react';

// Mock fetcher - replace with real API call when backend is ready
const fetchMakerOpportunities = async (): Promise<MakerOpportunity[]> => {
  // Simulate network delay
  await new Promise(resolve => setTimeout(resolve, 500));
  return generateMockMakerOpportunities();
};

export function useMakerData() {
  const excludedExchanges = useAppStore(state => state.excludedExchanges);
  const userSettings = useAppStore(state => state.userSettings);

  const { data, error, isLoading, mutate } = useSWR<MakerOpportunity[]>(
    '/maker-opportunities',
    fetchMakerOpportunities,
    {
      refreshInterval: 10000, // Refresh every 10 seconds
      revalidateOnFocus: true,
    }
  );

  const filteredOpportunities = useMemo(() => {
    if (!data) return [];

    return data.filter(opp => {
      // Filter by excluded exchanges
      if (excludedExchanges.length > 0 && excludedExchanges.includes(opp.exchange)) {
        return false;
      }

      // Filter by allowed exchanges
      const allowedExchanges = userSettings.autoTrade?.allowedExchanges || [];
      if (allowedExchanges.length > 0 && !allowedExchanges.includes(opp.exchange)) {
        return false;
      }

      return true;
    });
  }, [data, excludedExchanges, userSettings.autoTrade?.allowedExchanges]);

  return {
    opportunities: filteredOpportunities,
    isLoading,
    error,
    refresh: mutate,
    totalCount: data?.length || 0,
    filteredCount: filteredOpportunities.length,
  };
}
