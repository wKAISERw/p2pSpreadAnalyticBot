import { useMemo } from 'react';
import { ArbitrageOpportunity } from '../types';
import { useAppStore } from '../store';

export type SortOption = 'spread' | 'profit' | 'deal' | 'risk';

interface UseSpreadFiltersOptions {
  sortBy: SortOption;
}

export function useSpreadFilters(
  opportunities: ArbitrageOpportunity[] | undefined,
  options: UseSpreadFiltersOptions
) {
  const userSettings = useAppStore(state => state.userSettings);
  const excludedExchanges = useAppStore(state => state.excludedExchanges);

  const filteredAndSorted = useMemo(() => {
    if (!opportunities) return [];

    return opportunities
      // Filter by min spread
      .filter(opp => opp.netSpread >= userSettings.minSpread)
      // Filter by allowed exchanges (include list)
      .filter(opp => {
        const allowedExchanges = userSettings.autoTrade?.allowedExchanges || [];
        if (allowedExchanges.length === 0) return true;
        return (
          allowedExchanges.includes(opp.buyOrder.exchange) &&
          allowedExchanges.includes(opp.sellOrder.exchange)
        );
      })
      // Filter by excluded exchanges
      .filter(opp => {
        if (excludedExchanges.length === 0) return true;
        return (
          !excludedExchanges.includes(opp.buyOrder.exchange) &&
          !excludedExchanges.includes(opp.sellOrder.exchange)
        );
      })
      // Sort
      .sort((a, b) => {
        switch (options.sortBy) {
          case 'profit':
            return b.netProfit - a.netProfit;
          case 'deal':
            return b.dealAmount - a.dealAmount;
          case 'risk':
            return (
              (b.buyOrder.riskScore || 0) + (b.sellOrder.riskScore || 0) -
              ((a.buyOrder.riskScore || 0) + (a.sellOrder.riskScore || 0))
            );
          case 'spread':
          default:
            return b.netSpread - a.netSpread;
        }
      });
  }, [opportunities, userSettings.minSpread, userSettings.autoTrade?.allowedExchanges, excludedExchanges, options.sortBy]);

  return {
    opportunities: filteredAndSorted,
    totalCount: opportunities?.length || 0,
    filteredCount: filteredAndSorted.length,
    hasExclusions: excludedExchanges.length > 0,
  };
}
