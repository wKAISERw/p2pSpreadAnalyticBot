import React from 'react';
import useSWR from 'swr';
import { Ban, AlertTriangle, Lightbulb } from 'lucide-react';
import { api } from '../../services/api';
import { ReadinessCheck, ReadinessLevel, ReadinessReport } from '../../types';
import { useAppStore } from '../../store';
import { cn } from '../../lib/utils';

/**
 * Що завадить режиму працювати так, як його налаштували.
 *
 * Ці перевірки жили в циклі сканера й спрацьовували вже після запуску:
 * людина вмикала режим і чекала, а причина тиші лежала в налаштуваннях —
 * обрано банки, карток яких немає; обсяг більший за все, що є на картках;
 * місячна межа банку майже вибрана.
 *
 * Порядок перевірок збігається з тим, у якому їх робить движок, тож
 * попередження називає ту саму причину, що потім прилетить у статистику
 * відмов. Розбіжність між «на екрані все гаразд» і «сканер мовчить» була б
 * гіршою за відсутність перевірки.
 */
export function ReadinessNotice({ mode }: { mode?: string }) {
  const telegramId = useAppStore(state => state.auth?.telegramId);

  const { data } = useSWR<ReadinessReport>(
    telegramId ? ['/taker/readiness', mode ?? ''] : null,
    () => api.getTakerReadiness(mode),
    { refreshInterval: 60000, shouldRetryOnError: false }
  );

  const checks = data?.checks ?? [];
  if (checks.length === 0) return null;

  // Від найважчого до найлегшого: блокер міняє рішення, note — просто фон.
  const order: ReadinessLevel[] = ['blocker', 'warning', 'note'];
  const sorted = [...checks].sort(
    (a, b) => order.indexOf(a.level) - order.indexOf(b.level)
  );

  return (
    <div
      className={cn(
        'rounded-2xl border p-4',
        data?.hasBlockers
          ? 'bg-red-500/10 border-red-500/20'
          : 'bg-slate-900/50 border-slate-800/60'
      )}
    >
      <div className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">
        {data?.hasBlockers ? 'Алертів не буде' : 'Перевірка налаштувань'}
      </div>

      <div className="space-y-2.5">
        {sorted.map((check, i) => (
          <CheckRow
            key={`${check.level}-${i}`}
            check={check}
            showMode={(data?.modes?.length ?? 0) > 1}
          />
        ))}
      </div>
    </div>
  );
}

const TONE: Record<ReadinessLevel, { icon: React.ReactNode; text: string }> = {
  blocker: { icon: <Ban className="w-4 h-4" />, text: 'text-red-400' },
  warning: { icon: <AlertTriangle className="w-4 h-4" />, text: 'text-orange-400' },
  note: { icon: <Lightbulb className="w-4 h-4" />, text: 'text-slate-500' },
};

const MODE_LABELS: Record<string, string> = {
  TAKER_BUY: 'купівля',
  TAKER_SELL: 'продаж',
};

const CheckRow: React.FC<{ check: ReadinessCheck; showMode: boolean }> = ({
  check, showMode,
}) => {
  const tone = TONE[check.level] ?? TONE.note;

  return (
    <div className="flex items-start gap-2.5">
      <span className={cn('shrink-0 mt-0.5', tone.text)}>{tone.icon}</span>
      <div className="min-w-0">
        <div className="text-xs text-slate-200 leading-snug">
          {/* Режимів може бути кілька одночасно — без позначки незрозуміло,
              якого з них стосується попередження. */}
          {showMode && check.mode && (
            <span className="text-slate-500">
              {MODE_LABELS[check.mode] ?? check.mode}:{' '}
            </span>
          )}
          {check.text}
        </div>
        {check.hint && (
          <div className="text-[11px] text-slate-500 leading-snug mt-0.5">
            {check.hint}
          </div>
        )}
      </div>
    </div>
  );
};
