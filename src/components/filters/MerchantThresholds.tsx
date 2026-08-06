import React, { useEffect, useState } from 'react';
import { Users, Gift, Info, Loader2 } from 'lucide-react';
import useSWR from 'swr';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import {
  BlacklistMode, MerchantThresholdsFull, UsedSubsidy, UserFilters, VerifiedFilter,
} from '../../types';

/**
 * Пороги мерчантів — повний набір, який читають alert_dispatcher і
 * taker_scanner. Правило для біржі перебиває загальне (`ex_filters or mf`),
 * тому нуль тут означає «не обмежувати», а не «заборонити все».
 */

const VERIFIED: { value: VerifiedFilter; label: string }[] = [
  { value: 'all', label: 'Усі' },
  { value: 'verified', label: 'Тільки верифіковані' },
  { value: 'unverified', label: 'Тільки неверифіковані' },
];

const BLACKLIST: { value: BlacklistMode; label: string; hint: string }[] = [
  { value: 'block', label: 'Блокувати', hint: 'Не показувати взагалі' },
  { value: 'hide', label: 'Приховати', hint: 'Ховати зі списку, але не рахувати блоком' },
  { value: 'show', label: 'Показувати', hint: 'Показувати з позначкою ризику' },
];

interface Props {
  filters: UserFilters;
  exchangeNames: string[];
  onSaved: () => void;
}

export function MerchantThresholds({ filters, exchangeNames, onSaved }: Props) {
  const [scope, setScope] = useState('global');
  const [draft, setDraft] = useState<MerchantThresholdsFull>({});
  const [saving, setSaving] = useState(false);

  const stored: MerchantThresholdsFull =
    scope === 'global'
      ? (filters.merchantFilters ?? {})
      : (filters.exchangeMerchantFilters?.[scope] ?? {});

  useEffect(() => { setDraft({}); }, [scope, filters]);

  const val = <K extends keyof MerchantThresholdsFull>(key: K, fallback: any) =>
    (draft[key] !== undefined ? draft[key] : (stored[key] ?? fallback));

  const set = <K extends keyof MerchantThresholdsFull>(key: K, v: MerchantThresholdsFull[K]) =>
    setDraft(prev => ({ ...prev, [key]: v }));

  const hasChanges = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      const result = await api.updateMerchantFilters({
        ...draft,
        exchange: scope === 'global' ? undefined : scope,
      });
      toast.success(
        scope === 'global'
          ? `Загальні пороги збережено: ${result.updated.join(', ')}`
          : `Пороги для ${scope} збережено`
      );
      setDraft({});
      onSaved();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <Users className="w-5 h-5 text-orange-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Пороги мерчантів</h2>
            <p className="text-xs text-slate-400">
              Алерт прийде, тільки якщо обидва мерчанти проходять критерії
            </p>
          </div>
        </div>

        <button
          onClick={save}
          disabled={!hasChanges || saving}
          className="flex items-center gap-2 px-5 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-all"
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Зберегти
        </button>
      </div>

      {/* Область дії */}
      <div className="flex flex-wrap gap-2 mb-5">
        <Chip label="Загальні" active={scope === 'global'} onClick={() => setScope('global')} />
        {exchangeNames.map(name => (
          <Chip
            key={name}
            label={name}
            active={scope === name}
            dot={Boolean(filters.exchangeMerchantFilters?.[name])}
            onClick={() => setScope(name)}
          />
        ))}
      </div>

      {scope !== 'global' && (
        <Hint>
          Правило для {scope} перебиває загальне. Нуль означає «взяти загальне значення».
        </Hint>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
        <Field label="Мін. угод за місяць" sub="min_orders · 0 = без обмежень">
          <Num value={val('minOrders', 0)} onChange={v => set('minOrders', v)} />
        </Field>
        <Field label="Мін. рейтинг (%)" sub="min_rate">
          <Num value={val('minRate', 0)} onChange={v => set('minRate', v)} step={0.1} />
        </Field>
        <Field label="Мін. % позитивних" sub="min_positive_rate">
          <Num value={val('minPositiveRate', 0)} onChange={v => set('minPositiveRate', v)} step={0.1} />
        </Field>
        <Field label="Мін. вік акаунту (днів)" sub="min_account_age_days">
          <Num value={val('minAccountAgeDays', 0)} onChange={v => set('minAccountAgeDays', v)} />
        </Field>
        <Field label="Макс. офлайн (хв)" sub="max_offline_mins">
          <Num value={val('maxOfflineMins', 0)} onChange={v => set('maxOfflineMins', v)} />
        </Field>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
        <Field label="Статус верифікації" sub="verified_filter">
          <div className="flex flex-wrap gap-2">
            {VERIFIED.map(v => (
              <Chip
                key={v.value}
                label={v.label}
                active={val('verifiedFilter', 'all') === v.value}
                onClick={() => set('verifiedFilter', v.value)}
              />
            ))}
          </div>
        </Field>

        <Field label="Чорний список" sub="blacklist_mode">
          <div className="flex flex-wrap gap-2">
            {BLACKLIST.map(b => (
              <Chip
                key={b.value}
                label={b.label}
                hint={b.hint}
                active={val('blacklistMode', 'block') === b.value}
                onClick={() => set('blacklistMode', b.value)}
              />
            ))}
          </div>
        </Field>
      </div>

      <Subsidies />
    </section>
  );
}

/** Витрачена субсидія новачка більше не показується сканером. */
function Subsidies() {
  const { data: subsidies } = useSWR<UsedSubsidy[]>(
    '/user/subsidies',
    () => api.getSubsidies(),
    { shouldRetryOnError: false }
  );

  return (
    <div className="mt-6 pt-6 border-t border-slate-800/70">
      <div className="flex items-center gap-2 mb-2">
        <Gift className="w-4 h-4 text-pink-400" />
        <span className="text-sm font-bold text-white">Субсидії новачків</span>
      </div>

      {!subsidies?.length ? (
        <p className="text-xs text-slate-500">
          Ще жодної не витрачено — сканер показуватиме такі пропозиції на всіх біржах.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {subsidies.map(s => (
            <div
              key={s.exchange}
              className="px-3 py-1.5 rounded-xl bg-slate-950 border border-slate-800 text-xs"
              title="Використана субсидія більше не показується в алертах"
            >
              <span className="font-bold text-slate-300">{s.exchange}</span>
              <span className="text-slate-500"> · {s.used.join(', ')}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Дрібниці ─────────────────────────────────────────────────────────────

function Field({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm font-bold text-white mb-0.5">{label}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono mb-2">{sub}</div>}
      {children}
    </div>
  );
}

function Num({ value, onChange, step = 1 }: { value: number; onChange: (v: number) => void; step?: number }) {
  return (
    <input
      type="number"
      step={step}
      value={Number.isFinite(value) ? value : 0}
      onChange={e => onChange(parseFloat(e.target.value) || 0)}
      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all tabular-nums"
    />
  );
}

const Chip: React.FC<{
  label: string; active: boolean; onClick: () => void; hint?: string; dot?: boolean;
}> = ({ label, active, onClick, hint, dot }) => (
  <button
    onClick={onClick}
    title={hint}
    className={cn(
      'px-4 py-2 rounded-xl text-xs font-bold border transition-all',
      active
        ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
        : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
    )}
  >
    {label}
    {dot && <span className="ml-1.5 text-accent-400">●</span>}
  </button>
);

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 text-[11px] text-slate-500 leading-snug">
      <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  );
}
