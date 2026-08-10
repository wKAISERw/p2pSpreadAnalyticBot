import React from 'react';
import useSWR from 'swr';
import { Coins, RefreshCw, HelpCircle } from 'lucide-react';
import { api } from '../../services/api';
import { ExchangeWallets, UsdtInventory as Inventory } from '../../types';
import { useAppStore } from '../../store';
import { cn } from '../../lib/utils';

const usdt = (v: number) => `${v.toFixed(2)} USDT`;

/**
 * Де саме лежить USDT — по біржах і по гаманцях.
 *
 * «Є на Bybit 500 USDT» не означає «можу продати зараз»: після купівлі на
 * P2P монети падають на спот, а продаються з фандингу. Клієнти бірж
 * зливають обидва гаманці в одне число, тож різницю не було видно ніде —
 * вона з'ясовувалась уже під 15-хвилинний таймер угоди.
 *
 * Три колонки — це три різні відстані до угоди, а не три однакові кошики.
 */
export function UsdtInventory() {
  const telegramId = useAppStore(state => state.auth?.telegramId);

  const { data, isValidating, mutate } = useSWR<Inventory>(
    telegramId ? '/inventory/usdt' : null,
    () => api.getUsdtInventory(),
    { refreshInterval: 120000, shouldRetryOnError: false }
  );

  if (!data) return null;

  // «Не знаємо» і «нуль» — різні речі: нуль веде до висновку «треба
  // переказувати», невідоме не веде ні до якого.
  if (!data.known) {
    return (
      <div className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-4">
        <div className="flex items-center gap-2 mb-2">
          <Coins className="w-4 h-4 text-slate-400" />
          <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
            USDT на біржах
          </span>
        </div>
        <p className="text-xs text-slate-500 leading-snug">
          Балансів дістати не вдалось — немає ключів або біржі не відповіли.
          Це не означає, що монет немає.
        </p>
      </div>
    );
  }

  const rows = data.exchanges.filter(e => e.total > 0);
  const t = data.totals;

  return (
    <div className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Coins className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
          USDT на біржах
        </span>
        <button
          onClick={() => mutate()}
          title="Оновити баланси"
          className="ml-auto text-slate-500 hover:text-slate-300 transition-colors"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', isValidating && 'animate-spin')} />
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="text-xs text-slate-500">На підключених біржах USDT немає.</p>
      ) : (
        <>
          <div className="space-y-2">
            {rows.map(row => (
              <ExchangeRow key={row.exchange} row={row} />
            ))}
          </div>

          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 pt-3 border-t border-slate-800 text-[11px]">
            <span className="text-accent-400 font-bold tabular-nums">
              {usdt(t.funding ?? 0)} готово до продажу
            </span>
            {(t.spot ?? 0) > 0 && (
              <span className="text-slate-400 tabular-nums">
                {usdt(t.spot ?? 0)} на споті — один переказ усередині біржі
              </span>
            )}
            {(t.earn ?? 0) > 0 && (
              <span className="text-slate-500 tabular-nums">
                {usdt(t.earn ?? 0)} в Earn — спершу викупити
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}

const ExchangeRow: React.FC<{ row: ExchangeWallets }> = ({ row }) => (
  <div className="rounded-xl bg-slate-950/50 border border-slate-800/60 px-3 py-2">
    <div className="flex items-baseline justify-between gap-2 mb-1.5">
      <span className="text-xs font-bold text-slate-200">{row.exchange}</span>
      <span className="text-[11px] tabular-nums text-slate-500">{usdt(row.total)}</span>
    </div>

    <div className="grid grid-cols-3 gap-2 text-[10px]">
      <Slot label="Фандинг" value={row.funding} tone="ready" hint="продається зараз" />
      <Slot label="Спот" value={row.spot} hint="переказ усередині біржі" />
      <Slot
        label="Earn"
        value={row.earn}
        unknown={!row.earnKnown}
        hint={row.earnKnown ? 'спершу викупити' : 'на цій біржі не бачимо'}
      />
    </div>
  </div>
);

function Slot({
  label, value, tone, hint, unknown,
}: {
  label: string;
  value: number;
  tone?: 'ready';
  hint: string;
  unknown?: boolean;
}) {
  return (
    <div title={hint}>
      <div className="uppercase tracking-wider text-slate-600 flex items-center gap-1">
        {label}
        {unknown && <HelpCircle className="w-2.5 h-2.5" />}
      </div>
      <div
        className={cn(
          'font-bold tabular-nums',
          unknown ? 'text-slate-600' : tone === 'ready' && value > 0
            ? 'text-accent-400'
            : 'text-slate-400'
        )}
      >
        {unknown ? '—' : value.toFixed(2)}
      </div>
    </div>
  );
}
