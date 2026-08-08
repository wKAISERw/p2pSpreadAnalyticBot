import React from 'react';
import { motion } from 'motion/react';
import { ArrowRightLeft, TrendingDown, TrendingUp, Columns2, Store } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useAppStore, DashboardView } from '../../store';

/**
 * Що показує дашборд.
 *
 * Свідомо відв'язано від scanner_mode бота. Це різні питання:
 * «що бот шле мені в Telegram» і «на що я зараз дивлюсь у браузері».
 * Раніше вони були злиті — щоб подивитись бік купівлі, доводилось
 * перемкнути режим сканера, тобто змінити те, що приходить у чат.
 *
 * Тейкер-сторони можна дивитись разом («Обидві»): у режимі бота так не
 * буває — scanner_mode один, — але для ока обмеження немає.
 */

const VIEWS: { value: DashboardView; label: string; icon: React.ElementType; hint: string }[] = [
  {
    value: 'spread',
    label: 'Спреди',
    icon: ArrowRightLeft,
    hint: "Зв'язки купівля→продаж: обидві ноги одразу",
  },
  {
    value: 'buy',
    label: 'Купівля',
    icon: TrendingDown,
    hint: 'Чужі оголошення про продаж — ти купуєш USDT',
  },
  {
    value: 'sell',
    label: 'Продаж',
    icon: TrendingUp,
    hint: 'Чужі оголошення про купівлю — ти продаєш USDT',
  },
  {
    value: 'both',
    label: 'Обидві',
    icon: Columns2,
    hint: 'Купівля і продаж у двох колонках',
  },
  {
    value: 'maker',
    label: 'Maker',
    icon: Store,
    hint: 'Робоче місце для власного оголошення (демо-дані)',
  },
];

export function ViewToggle() {
  const view = useAppStore(state => state.dashboardView);
  const setView = useAppStore(state => state.setDashboardView);

  return (
    <div className="flex bg-slate-950 rounded-xl p-1 border border-slate-800 overflow-x-auto [&::-webkit-scrollbar]:hidden [scrollbar-width:none]">
      {VIEWS.map(({ value, label, icon: Icon, hint }) => {
        const active = view === value;
        return (
          <button
            key={value}
            onClick={() => setView(value)}
            title={hint}
            className={cn(
              'relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors whitespace-nowrap focus:ring-2 focus:ring-accent-500/50 outline-none',
              active
                ? value === 'maker'
                  ? 'text-purple-400'
                  : 'text-accent-400'
                : 'text-slate-500 hover:text-slate-300'
            )}
          >
            <Icon className="w-3.5 h-3.5" />
            <span>{label}</span>
            {active && (
              <motion.div
                layoutId="dashboard-view-indicator"
                className={cn(
                  'absolute inset-0 rounded-lg -z-10',
                  value === 'maker' ? 'bg-purple-500/15' : 'bg-accent-500/15'
                )}
                transition={{ type: 'spring', duration: 0.3 }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}
