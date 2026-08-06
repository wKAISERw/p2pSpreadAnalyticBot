import React from 'react';
import { Radio, TimerReset, CircleSlash } from 'lucide-react';
import { ExchangeStatus } from '../../types';
import { useExchanges } from '../../hooks/useExchanges';
import { cn } from '../../lib/utils';

/**
 * Стан бірж із GET /api/v1/exchanges (ExchangeManager).
 *
 * Біржа може бути вимкнена вручну або сама піти в cooldown після серії
 * відмов — без цього блоку порожній дашборд виглядав як «немає спредів»,
 * хоча насправді половина бірж не опитувалась.
 */
export function ExchangeHealth() {
  const { exchanges, error } = useExchanges();

  if (error || !exchanges.length) return null;

  return (
    <div className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Radio className="w-4 h-4 text-slate-400" />
        <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
          Доступність бірж
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {exchanges.map((ex) => (
          <ExchangeChip key={ex.name} status={ex} />
        ))}
      </div>
    </div>
  );
}

const ExchangeChip: React.FC<{ status: ExchangeStatus }> = ({ status }) => {
  const { name, enabled, isCooldown, cooldownRemainingH, disabledReason, failures } = status;

  const title = enabled
    ? failures > 0
      ? `${name}: працює, послідовних відмов — ${failures}`
      : `${name}: працює`
    : isCooldown
      ? `${name}: cooldown ще ${cooldownRemainingH} год${disabledReason ? ` — ${disabledReason}` : ''}`
      : `${name}: вимкнено${disabledReason ? ` — ${disabledReason}` : ''}`;

  return (
    <div
      title={title}
      className={cn(
        'flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs font-bold transition-colors',
        enabled
          ? 'bg-accent-500/10 border-accent-500/20 text-accent-400'
          : isCooldown
            ? 'bg-orange-500/10 border-orange-500/20 text-orange-400'
            : 'bg-slate-800 border-slate-700 text-slate-400'
      )}
    >
      {enabled ? (
        <span className="w-2 h-2 rounded-full bg-accent-500 shrink-0" />
      ) : isCooldown ? (
        <TimerReset className="w-3.5 h-3.5 shrink-0" />
      ) : (
        <CircleSlash className="w-3.5 h-3.5 shrink-0" />
      )}
      <span>{name}</span>
      {!enabled && isCooldown && cooldownRemainingH > 0 && (
        <span className="tabular-nums opacity-80">{cooldownRemainingH}г</span>
      )}
      {enabled && failures > 0 && (
        <span className="tabular-nums text-orange-400" title="Послідовних відмов">
          ⚠{failures}
        </span>
      )}
    </div>
  );
};
