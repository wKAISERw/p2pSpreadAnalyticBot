import React from 'react';
import { motion } from 'motion/react';
import { ArrowRightLeft, TrendingDown, TrendingUp, Columns2, Store, Crosshair } from 'lucide-react';
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
 * Верхній рівень — рід заняття (спред, тейкер, мейкер), нижній — бік
 * усередині нього. Спершу всі п'ять кнопок стояли в один ряд, і виходило,
 * що «Купівля», «Продаж» і «Обидві» — щось того самого порядку, що й
 * «Maker», хоча насправді це його підвиди. На вузькому екрані ряд ще й не
 * вміщався, тож частина режимів просто ховалась за краєм.
 */

type Group = 'spread' | 'taker' | 'maker';

const GROUPS: { value: Group; label: string; icon: React.ElementType; hint: string }[] = [
  {
    value: 'spread',
    label: 'Спред',
    icon: ArrowRightLeft,
    hint: "Зв'язки купівля→продаж: обидві ноги одразу",
  },
  {
    value: 'taker',
    label: 'Тейкер',
    icon: Crosshair,
    hint: 'Чужі оголошення — ти береш готове',
  },
  {
    value: 'maker',
    label: 'Мейкер',
    icon: Store,
    hint: 'Робоче місце для власного оголошення (демо-дані)',
  },
];

const TAKER_SIDES: { value: DashboardView; label: string; icon: React.ElementType; hint: string }[] = [
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
];

/** До якої групи належить поточний вигляд. */
function groupOf(view: DashboardView): Group {
  if (view === 'spread') return 'spread';
  if (view === 'maker') return 'maker';
  return 'taker';
}

export function ViewToggle() {
  const view = useAppStore(state => state.dashboardView);
  const setView = useAppStore(state => state.setDashboardView);
  const group = groupOf(view);

  // Останній обраний бік запам'ятовується поверненням у ту саму вкладку:
  // перемкнувся на спред і назад — бачиш те, що дивився, а не дефолт.
  const [lastSide, setLastSide] = React.useState<DashboardView>(
    group === 'taker' ? view : 'both'
  );

  const pickGroup = (next: Group) => {
    if (next === 'taker') setView(lastSide);
    else setView(next);
  };

  const pickSide = (side: DashboardView) => {
    setLastSide(side);
    setView(side);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex bg-slate-950 rounded-xl p-1 border border-slate-800">
        {GROUPS.map(({ value, label, icon: Icon, hint }) => {
          const active = group === value;
          return (
            <button
              key={value}
              onClick={() => pickGroup(value)}
              title={hint}
              className={cn(
                'relative flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors whitespace-nowrap focus:ring-2 focus:ring-accent-500/50 outline-none',
                active
                  ? value === 'maker' ? 'text-purple-400' : 'text-accent-400'
                  : 'text-slate-500 hover:text-slate-300'
              )}
            >
              <Icon className="w-3.5 h-3.5 shrink-0" />
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

      {group === 'taker' && (
        <div className="flex bg-slate-950/60 rounded-xl p-1 border border-slate-800/60">
          {TAKER_SIDES.map(({ value, label, icon: Icon, hint }) => {
            const active = view === value;
            return (
              <button
                key={value}
                onClick={() => pickSide(value)}
                title={hint}
                className={cn(
                  'relative flex-1 flex items-center justify-center gap-1.5 px-3 py-1 rounded-lg text-[11px] font-bold transition-colors whitespace-nowrap focus:ring-2 focus:ring-accent-500/50 outline-none',
                  active ? 'text-accent-400' : 'text-slate-500 hover:text-slate-300'
                )}
              >
                <Icon className="w-3 h-3 shrink-0" />
                <span>{label}</span>
                {active && (
                  <motion.div
                    layoutId="dashboard-side-indicator"
                    className="absolute inset-0 rounded-lg bg-accent-500/10 -z-10"
                    transition={{ type: 'spring', duration: 0.3 }}
                  />
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
