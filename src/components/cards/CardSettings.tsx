import React, { useState } from 'react';
import useSWR from 'swr';
import { SlidersHorizontal, Bell, Loader2, Info, Save } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { Card, LimitField, MonoTracker, TrackerMode } from '../../types';

const LIMIT_FIELDS: { key: LimitField; label: string; step: number }[] = [
  { key: 'daily_out_max', label: 'Витрати / доба', step: 1000 },
  { key: 'daily_in_max', label: 'Надходження / доба', step: 1000 },
  { key: 'monthly_out_max', label: 'Витрати / місяць', step: 5000 },
  { key: 'monthly_in_max', label: 'Надходження / місяць', step: 5000 },
  { key: 'max_single_tx_out', label: 'Макс. одна витрата', step: 500 },
  { key: 'max_single_tx_in', label: 'Макс. одне надходження', step: 500 },
  { key: 'max_tx_per_day', label: 'Транзакцій / доба', step: 1 },
  { key: 'cooldown_hours', label: 'Пауза (год)', step: 1 },
];

const TRACKER_FIELDS: { key: string; label: string }[] = [
  { key: 'amount', label: 'Сума' },
  { key: 'sender', label: 'Відправник' },
  { key: 'comment', label: 'Коментар' },
  { key: 'time', label: 'Час' },
  { key: 'card', label: 'Картка' },
  { key: 'balance', label: 'Новий залишок' },
  { key: 'p2p', label: "Зв'язок з P2P-ордером" },
];

/**
 * Персональні налаштування картки: власні ліміти (перебивають банківські)
 * та сповіщення Monobank.
 */
export function CardSettings({ card, onSaved }: { card: Card; onSaved: () => void }) {
  const [tab, setTab] = useState<'limits' | 'tracker'>('limits');

  return (
    <div className="px-6 py-5 space-y-4">
      <div className="flex gap-1 bg-slate-950 border border-slate-800 rounded-xl p-1 w-fit">
        {([
          { value: 'limits', label: 'Ліміти', icon: SlidersHorizontal },
          { value: 'tracker', label: 'Сповіщення', icon: Bell },
        ] as const).map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            onClick={() => setTab(value)}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors',
              tab === value ? 'bg-slate-800 text-white' : 'text-slate-500 hover:text-slate-300'
            )}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {tab === 'limits'
        ? <LimitsEditor card={card} onSaved={onSaved} />
        : <TrackerEditor cardId={card.id} />}
    </div>
  );
}

function LimitsEditor({ card, onSaved }: { card: Card; onSaved: () => void }) {
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);

  const isCustom = Boolean((card as any).isCustomLimits);
  const value = (key: LimitField) =>
    draft[key] !== undefined ? draft[key] : Number(card.limits?.[key] ?? 0);

  const hasChanges = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await api.setCardLimits(card.id, draft);
      toast.success('Ліміти картки збережено');
      setDraft({});
      onSaved();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  const toggleCustom = async (enabled: boolean) => {
    try {
      await api.toggleCardCustomLimits(card.id, enabled);
      toast.success(enabled ? 'Власні ліміти увімкнено' : 'Повернено до лімітів банку');
      onSaved();
    } catch (e: any) {
      toast.error(e?.message ?? 'Не вдалось');
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <label className="flex items-center gap-2.5 cursor-pointer">
          <div
            onClick={() => toggleCustom(!isCustom)}
            className={cn(
              'w-9 h-5 rounded-full relative transition-colors',
              isCustom ? 'bg-accent-500' : 'bg-slate-700'
            )}
          >
            <div className={cn(
              'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all',
              isCustom ? 'right-0.5' : 'left-0.5'
            )} />
          </div>
          <span className="text-xs font-bold text-slate-300">Власні ліміти</span>
        </label>

        <button
          onClick={save}
          disabled={!hasChanges || saving}
          className="flex items-center gap-1.5 px-4 py-1.5 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-lg transition-all"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          Зберегти
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {LIMIT_FIELDS.map(field => (
          <div key={field.key}>
            <div className="text-[11px] text-slate-400 mb-1">{field.label}</div>
            <input
              type="number"
              step={field.step}
              value={value(field.key)}
              onChange={e =>
                setDraft(prev => ({ ...prev, [field.key]: parseFloat(e.target.value) || 0 }))
              }
              className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white focus:border-accent-500 outline-none tabular-nums"
            />
          </div>
        ))}
      </div>

      <Hint>
        Збереження вмикає власні ліміти автоматично. Вимкни тумблер, щоб
        повернутись до загальних лімітів банку.
      </Hint>
    </div>
  );
}

function TrackerEditor({ cardId }: { cardId: string }) {
  const { data, isLoading, mutate } = useSWR<MonoTracker>(
    ['/mono-tracker', cardId],
    () => api.getMonoTracker(cardId),
    { shouldRetryOnError: false }
  );

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-slate-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Завантажую…
      </p>
    );
  }
  if (!data) return <p className="text-xs text-slate-500">Немає даних.</p>;

  const patch = async (body: Parameters<typeof api.updateMonoTracker>[1]) => {
    try {
      await api.updateMonoTracker(cardId, body);
      await mutate();
    } catch (e: any) {
      toast.error(e?.message ?? 'Не збережено');
    }
  };

  if (!data.hasToken) {
    return (
      <Hint>
        Токен Monobank для цієї картки не підключений — сповіщення не працюватимуть.
        Підключається в боті, меню «Картки».
      </Hint>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={() => patch({ enabled: !data.enabled })}
          className={cn(
            'flex items-center gap-2 px-4 py-2 rounded-xl border text-xs font-bold transition-colors',
            data.enabled
              ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
              : 'bg-slate-800 border-slate-700 text-slate-400'
          )}
        >
          <Bell className="w-3.5 h-3.5" />
          {data.enabled ? 'Сповіщення увімкнені' : 'Сповіщення вимкнені'}
        </button>

        {(['INCOME', 'ALL'] as TrackerMode[]).map(mode => (
          <button
            key={mode}
            onClick={() => patch({ mode })}
            title={mode === 'INCOME' ? 'Тільки надходження' : 'Усі транзакції'}
            className={cn(
              'px-3 py-2 rounded-xl text-xs font-bold border transition-all',
              data.mode === mode
                ? 'bg-slate-800 border-slate-600 text-white'
                : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
            )}
          >
            {mode === 'INCOME' ? 'Лише прихід' : 'Усі'}
          </button>
        ))}
      </div>

      <div>
        <div className="text-xs font-bold text-white mb-2">Що показувати в сповіщенні</div>
        <div className="flex flex-wrap gap-2">
          {TRACKER_FIELDS.map(field => (
            <button
              key={field.key}
              onClick={() =>
                patch({ fields: { ...data.fields, [field.key]: !data.fields[field.key] } })
              }
              className={cn(
                'px-3 py-1.5 rounded-lg text-[11px] font-bold border transition-all',
                data.fields[field.key]
                  ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                  : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
              )}
            >
              {field.label}
            </button>
          ))}
        </div>
      </div>

      {!data.hasWebhook && (
        <Hint>Вебхук Monobank ще не налаштований — сповіщення не приходитимуть.</Hint>
      )}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 mt-3 text-[11px] text-slate-500 leading-snug">
      <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  );
}
