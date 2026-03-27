import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Filter, X, ChevronDown, Ban, Check } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useAppStore } from '../../store';

const ALL_EXCHANGES = ['Binance', 'Bybit', 'OKX', 'MEXC'] as const;

interface FilterControlsProps {
  className?: string;
}

export function FilterControls({ className }: FilterControlsProps) {
  const [isOpen, setIsOpen] = useState(false);
  
  const userSettings = useAppStore(state => state.userSettings);
  const setUserSettings = useAppStore(state => state.setUserSettings);
  const excludedExchanges = useAppStore(state => state.excludedExchanges);
  const toggleExcludedExchange = useAppStore(state => state.toggleExcludedExchange);
  const setExcludedExchanges = useAppStore(state => state.setExcludedExchanges);

  const toggleIncludeExchange = (exchange: string) => {
    // 1. Беремо поточні налаштування або створюємо дефолтні, якщо їх немає
    const currentAutoTrade = userSettings.autoTrade || {
      enabled: false,
      maxTradeAmount: 5000,
      minSpread: 0.8,
      allowedExchanges: ['Bybit', 'OKX'], // дефолт
      maxRiskScore: 30,
      maxCapital: 15000
    };

    // 2. Беремо масив бірж (захист від undefined)
    const currentExchanges = currentAutoTrade.allowedExchanges || [];

    // 3. Додаємо або забираємо біржу
    const newExchanges = currentExchanges.includes(exchange)
      ? currentExchanges.filter(e => e !== exchange)
      : [...currentExchanges, exchange];

    // 4. Оновлюємо стейт
    setUserSettings({
      ...userSettings,
      autoTrade: { ...currentAutoTrade, allowedExchanges: newExchanges }
    });
  };

  const hasExclusions = excludedExchanges.length > 0;

  return (
    <div className={cn("relative", className)}>
      {/* Toggle Button */}
      <motion.button
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-bold transition-all border focus:ring-2 focus:ring-emerald-500/50 outline-none",
          hasExclusions 
            ? "bg-orange-500/10 border-orange-500/30 text-orange-400"
            : "bg-slate-950 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700"
        )}
      >
        <Filter className="w-3.5 h-3.5" />
        <span>Filters</span>
        {hasExclusions && (
          <span className="bg-orange-500/20 text-orange-400 px-1.5 py-0.5 rounded-md text-[10px] font-black tabular-nums">
            -{excludedExchanges.length}
          </span>
        )}
        <ChevronDown className={cn(
          "w-3 h-3 transition-transform",
          isOpen && "rotate-180"
        )} />
      </motion.button>

      {/* Dropdown Panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: -10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="absolute top-full mt-2 right-0 z-50 bg-slate-900 border border-slate-800 rounded-2xl p-4 shadow-xl w-72"
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-4">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Exchange Filters</span>
              <button 
                onClick={() => setIsOpen(false)}
                className="p-1 hover:bg-slate-800 rounded-lg transition-colors"
              >
                <X className="w-4 h-4 text-slate-500" />
              </button>
            </div>

            {/* Include Section */}
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Include</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ALL_EXCHANGES.map(ex => {
                  const isIncluded = userSettings.autoTrade?.allowedExchanges.includes(ex) ?? false;
                  return (
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      key={ex}
                      onClick={() => toggleIncludeExchange(ex)}
                      className={cn(
                        "px-2.5 py-1.5 rounded-xl text-xs font-bold transition-all border",
                        isIncluded
                          ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                          : "bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700"
                      )}
                    >
                      {ex}
                    </motion.button>
                  );
                })}
              </div>
            </div>

            {/* Exclude Section */}
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                <Ban className="w-3.5 h-3.5 text-orange-400" />
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Exclude</span>
              </div>
              <p className="text-[10px] text-slate-500 mb-2">
                Scan all EXCEPT these exchanges
              </p>
              <div className="flex flex-wrap gap-1.5">
                {ALL_EXCHANGES.map(ex => {
                  const isExcluded = excludedExchanges.includes(ex);
                  return (
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      key={ex}
                      onClick={() => toggleExcludedExchange(ex)}
                      className={cn(
                        "px-2.5 py-1.5 rounded-xl text-xs font-bold transition-all border",
                        isExcluded
                          ? "bg-red-500/10 border-red-500/30 text-red-400"
                          : "bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700"
                      )}
                    >
                      {isExcluded && <X className="w-3 h-3 mr-1 inline" />}
                      {ex}
                    </motion.button>
                  );
                })}
              </div>
            </div>

            {/* Quick Actions */}
            <div className="border-t border-slate-800 pt-3 flex items-center gap-2">
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.95 }}
                onClick={() => setExcludedExchanges([])}
                className="flex-1 px-3 py-2 bg-slate-950 border border-slate-800 rounded-xl text-xs font-bold text-slate-400 hover:text-white transition-colors"
              >
                Clear Exclusions
              </motion.button>
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.95 }}
                onClick={() => {
                  if (userSettings.autoTrade) {
                    setUserSettings({
                      ...userSettings,
                      autoTrade: { ...userSettings.autoTrade, allowedExchanges: [...ALL_EXCHANGES] }
                    });
                  }
                  setExcludedExchanges([]);
                }}
                className="flex-1 px-3 py-2 bg-emerald-500/10 border border-emerald-500/30 rounded-xl text-xs font-bold text-emerald-400 hover:bg-emerald-500/20 transition-colors"
              >
                Reset All
              </motion.button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
