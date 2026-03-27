import React from 'react';
import { motion } from 'motion/react';
import { HandCoins, Store } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useAppStore, TradingMode } from '../../store';

interface TradingModeToggleProps {
  className?: string;
}

export function TradingModeToggle({ className }: TradingModeToggleProps) {
  const tradingMode = useAppStore(state => state.tradingMode);
  const setTradingMode = useAppStore(state => state.setTradingMode);

  const modes: { value: TradingMode; label: string; icon: React.ReactNode; description: string }[] = [
    { 
      value: 'taker', 
      label: 'Taker', 
      icon: <HandCoins className="w-3.5 h-3.5" />,
      description: 'Buy from existing ads'
    },
    { 
      value: 'maker', 
      label: 'Maker', 
      icon: <Store className="w-3.5 h-3.5" />,
      description: 'Place your own ads'
    },
  ];

  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      <div className="flex bg-slate-950 rounded-xl p-1 border border-slate-800">
        {modes.map(mode => (
          <motion.button
            key={mode.value}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => setTradingMode(mode.value)}
            className={cn(
              "relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all focus:ring-2 focus:ring-emerald-500/50 outline-none",
              tradingMode === mode.value
                ? mode.value === 'maker'
                  ? "bg-purple-500/20 text-purple-400"
                  : "bg-emerald-500/20 text-emerald-400"
                : "text-slate-500 hover:text-slate-300"
            )}
            title={mode.description}
          >
            {mode.icon}
            <span>{mode.label}</span>
            {tradingMode === mode.value && (
              <motion.div
                layoutId="trading-mode-indicator"
                className={cn(
                  "absolute inset-0 rounded-lg -z-10",
                  mode.value === 'maker' ? "bg-purple-500/10" : "bg-emerald-500/10"
                )}
                transition={{ type: "spring", duration: 0.3 }}
              />
            )}
          </motion.button>
        ))}
      </div>
    </div>
  );
}
