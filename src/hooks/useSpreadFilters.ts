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
      // Поріг спреду — суто для показу. Те, що бот взагалі шукає й шле в
      // Telegram, задає min_spread_pct у розділі «Фільтри».
      .filter(opp => opp.netSpread >= userSettings.minSpread)
      /*
       * Приховані біржі.
       *
       * Раніше фільтрів було два: include-список `autoTrade.allowedExchanges`
       * і цей exclude-список. Вони жили в одній панелі, робили протилежні
       * речі й мали різні набори бірж — а include ще й приїжджав із
       * дефолтом ['Bybit','OKX'], тобто мовчки ховав решту. Лишився один
       * список: порожній = видно все.
       */
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
  }, [opportunities, userSettings.minSpread, excludedExchanges, options.sortBy]);

  return {
    opportunities: filteredAndSorted,
    totalCount: opportunities?.length || 0,
    filteredCount: filteredAndSorted.length,
    hasExclusions: excludedExchanges.length > 0,
  };
}
