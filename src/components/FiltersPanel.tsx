import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { SlidersHorizontal, Wallet, Percent, Building2, Users, Loader2, Save, Bell, BellOff } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { useExchanges } from '../hooks/useExchanges';
import { TakerSettings } from './filters/TakerSettings';
import { MerchantThresholds } from './filters/MerchantThresholds';
import { SniperRules } from './filters/SniperRules';
import {
  Bank, ScannerMode, SpreadStrategy, CapitalMode, UserFilters, UserFiltersPatch,
} from '../types';

const SCANNER_MODES: { value: ScannerMode; label: string; hint: string }[] = [
  { value: 'SPREAD', label: 'Спред', hint: 'Класичний пошук зв\'язок купівля→продаж' },
  { value: 'MAKER_BUY', label: 'Maker Buy', hint: 'Порада ціни для власного оголошення на купівлю' },
  { value: 'MAKER_SELL', label: 'Maker Sell', hint: 'Порада ціни продажу від ціни закупівлі' },
  { value: 'TAKER_BUY', label: 'Taker Buy', hint: 'Полювання на чужі оголошення про продаж' },
  { value: 'TAKER_SELL', label: 'Taker Sell', hint: 'Полювання на чужі оголошення про купівлю' },
];

const SPREAD_STRATEGIES: { value: SpreadStrategy; label: string }[] = [
  { value: 'min', label: 'Від мінімуму' },
  { value: 'max', label: 'До максимуму' },
  { value: 'range', label: 'Діапазон' },
];

/**
 * Персональні фільтри зі scanner_users — те саме, що меню «Фільтри» в боті.
 * Пишуться точково через POST /user/filters: ендпоінт оновлює лише передані
 * поля, тож редагування з сайту не затирає те, що виставлено в Telegram.
 */
export default function FiltersPanel() {
  // Особа береться з підтвердженої сесії — раніше тут був ID,
  // введений руками в налаштуваннях, тобто будь-який.
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const { names: exchangeNames } = useExchanges();

  const { data: filters, error, isLoading, mutate } = useSWR<UserFilters>(
    telegramId ? ['/user/filters', telegramId] : null,
    () => api.getUserFilters(telegramId!),
    { shouldRetryOnError: false }
  );
  const { data: banks = [] } = useSWR<Bank[]>('/banks', () => api.getBanks(), {
    shouldRetryOnError: false,
  });

  const [draft, setDraft] = useState<UserFiltersPatch>({});
  const [isSaving, setIsSaving] = useState(false);

  // Чернетка живе поверх завантажених фільтрів: показуємо draft ?? server.
  useEffect(() => { setDraft({}); }, [filters?.userId]);

  if (!telegramId) return <EmptyState text="Потрібен вхід через Telegram." />;
  if (isLoading) return <EmptyState text="Читаю фільтри з бота…" spinner />;
  if (error) {
    return <EmptyState text={`Не вдалось завантажити фільтри: ${(error as Error).message}`} />;
  }
  if (!filters) return null;

  const value = <K extends keyof UserFiltersPatch>(key: K, fallback: any) =>
    (draft[key] !== undefined ? draft[key] : fallback);

  const set = <K extends keyof UserFiltersPatch>(key: K, v: UserFiltersPatch[K]) =>
    setDraft(prev => ({ ...prev, [key]: v }));

  const toggleBank = (field: 'bankCodes' | 'buyBankCodes' | 'sellBankCodes', code: string, current: string[]) => {
    const next = current.includes(code) ? current.filter(c => c !== code) : [...current, code];
    set(field, next);
  };

  const activeMode = value('scannerMode', filters.scannerMode);
  const hasChanges = Object.keys(draft).length > 0;

  const save = async () => {
    setIsSaving(true);
    try {
      const result = await api.updateUserFilters(telegramId, draft);
      if (result.rejected?.length) {
        toast.warning(`Бекенд проігнорував: ${result.rejected.join(', ')}`);
      }
      toast.success(`Збережено: ${result.updated.join(', ') || 'нічого'}`);
      setDraft({});
      await mutate();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка запиту'}`);
    } finally {
      setIsSaving(false);
    }
  };

  const toggleAlerts = async () => {
    const next = !(filters.isAlertsActive ?? 1);
    try {
      await api.updateUserFilters(telegramId, { isAlertsActive: next });
      await mutate();
      toast.success(next ? 'Алерти увімкнено' : 'Алерти вимкнено');
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    }
  };

  const alertsOn = (filters.isAlertsActive ?? 1) === 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Фільтри</h1>
          <p className="text-sm text-slate-400">Персональні налаштування сканування · ID {filters.userId}</p>
        </div>

        <div className="flex items-center gap-2 sm:gap-3 shrink-0">
          <button
            onClick={toggleAlerts}
            className={cn(
              'flex items-center gap-2 px-3 sm:px-4 py-2 rounded-xl border text-xs sm:text-sm font-bold transition-colors whitespace-nowrap',
              alertsOn
                ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                : 'bg-slate-800 border-slate-700 text-slate-400'
            )}
          >
            {alertsOn ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
            {alertsOn ? 'Алерти увімкнені' : 'Алерти вимкнені'}
          </button>

          <button
            onClick={save}
            disabled={!hasChanges || isSaving}
            className="flex items-center gap-2 px-4 sm:px-6 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 disabled:hover:bg-accent-500 text-slate-950 text-xs sm:text-sm font-bold rounded-xl transition-all whitespace-nowrap"
          >
            {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Зберегти{hasChanges ? ` (${Object.keys(draft).length})` : ''}
          </button>
        </div>
      </div>

      {/* Режим сканера */}
      <Section icon={<SlidersHorizontal className="w-5 h-5 text-accent-400" />} title="Режим сканера">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {SCANNER_MODES.map(mode => {
            const active = value('scannerMode', filters.scannerMode) === mode.value;
            return (
              <button
                key={mode.value}
                onClick={() => set('scannerMode', mode.value)}
                className={cn(
                  'text-left p-4 rounded-2xl border transition-all',
                  active
                    ? 'bg-accent-500/10 border-accent-500/30'
                    : 'bg-slate-950/50 border-slate-800 hover:border-slate-700'
                )}
              >
                <div className={cn('font-bold text-sm mb-1', active ? 'text-accent-400' : 'text-white')}>
                  {mode.label}
                </div>
                <div className="text-xs text-slate-400 leading-snug">{mode.hint}</div>
              </button>
            );
          })}
        </div>
      </Section>

      {/* Тейкер-режими мають власний набір налаштувань — показуємо його
          замість спредових порогів, які в цих режимах не читаються. */}
      {(activeMode === 'TAKER_BUY' || activeMode === 'TAKER_SELL') && (
        <TakerSettings
          mode={activeMode}
          filters={filters as any}
          value={value}
          set={set}
          exchangeNames={exchangeNames}
        />
      )}

      {/* Капітал і спред */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Section icon={<Wallet className="w-5 h-5 text-blue-400" />} title="Капітал">
          <Field label="Робочий капітал (₴)" sub="working_capital">
            <NumberInput
              value={value('workingCapital', filters.capital)}
              onChange={v => set('workingCapital', v)}
            />
          </Field>
          <Field label="Мінімальна сума угоди (₴)" sub="min_amount_uah">
            <NumberInput
              value={value('minAmountUah', filters.minAmount)}
              onChange={v => set('minAmountUah', v)}
            />
          </Field>
          <Field label="Джерело капіталу" sub="capital_mode">
            <div className="flex gap-2">
              {(['manual', 'auto'] as CapitalMode[]).map(m => (
                <Chip
                  key={m}
                  active={value('capitalMode', filters.capitalMode) === m}
                  onClick={() => set('capitalMode', m)}
                  label={m === 'manual' ? 'Вручну' : 'З балансу карток'}
                />
              ))}
            </div>
          </Field>
        </Section>

        {activeMode === 'SPREAD' && (
        <Section icon={<Percent className="w-5 h-5 text-yellow-400" />} title="Спред">
          <Field label="Мінімальний спред (%)" sub="min_spread_pct">
            <NumberInput
              step={0.1}
              value={value('minSpreadPct', filters.minSpread)}
              onChange={v => set('minSpreadPct', v)}
            />
          </Field>
          <Field label="Максимальний спред (%)" sub="max_spread_pct · 0 = без обмеження">
            <NumberInput
              step={0.1}
              value={value('maxSpreadPct', filters.maxSpread)}
              onChange={v => set('maxSpreadPct', v)}
            />
          </Field>
          <Field label="Стратегія" sub="spread_strategy">
            <div className="flex flex-wrap gap-2">
              {SPREAD_STRATEGIES.map(s => (
                <Chip
                  key={s.value}
                  active={value('spreadStrategy', filters.spreadStrategy) === s.value}
                  onClick={() => set('spreadStrategy', s.value)}
                  label={s.label}
                />
              ))}
            </div>
          </Field>
        </Section>
        )}
      </div>

      {/* Банки */}
      <Section icon={<Building2 className="w-5 h-5 text-purple-400" />} title="Банки">
        <p className="text-xs text-slate-400 -mt-2 mb-4">
          Загальний список використовується, коли окремі списки для купівлі й продажу порожні.
        </p>
        <BankPicker
          title="Загальні"
          banks={banks}
          selected={value('bankCodes', filters.bankCodes)}
          onToggle={code => toggleBank('bankCodes', code, value('bankCodes', filters.bankCodes))}
        />
        <BankPicker
          title="Тільки для купівлі"
          banks={banks}
          selected={value('buyBankCodes', filters.buyBankCodes)}
          onToggle={code => toggleBank('buyBankCodes', code, value('buyBankCodes', filters.buyBankCodes))}
        />
        <BankPicker
          title="Тільки для продажу"
          banks={banks}
          selected={value('sellBankCodes', filters.sellBankCodes)}
          onToggle={code => toggleBank('sellBankCodes', code, value('sellBankCodes', filters.sellBankCodes))}
        />
      </Section>

      <MerchantThresholds
        filters={filters}
        exchangeNames={exchangeNames}
        onSaved={mutate}
      />

      <SniperRules exchangeNames={exchangeNames} />
    </div>
  );
}

// ─── Дрібні будівельні блоки ──────────────────────────────────────────────

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex items-center gap-3 mb-5">
        <div className="p-2 bg-slate-800/60 rounded-xl">{icon}</div>
        <h2 className="text-lg font-bold text-white">{title}</h2>
      </div>
      <div className="space-y-4">{children}</div>
    </motion.section>
  );
}

function Field({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm font-bold text-white mb-0.5">{label}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono mb-2">{sub}</div>}
      {children}
    </div>
  );
}

function NumberInput({ value, onChange, step = 1 }: { value: number; onChange: (v: number) => void; step?: number }) {
  return (
    <input
      type="number"
      step={step}
      value={Number.isFinite(value) ? value : 0}
      onChange={e => onChange(step < 1 ? parseFloat(e.target.value) || 0 : parseInt(e.target.value, 10) || 0)}
      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all tabular-nums"
    />
  );
}

const Chip: React.FC<{ active: boolean; onClick: () => void; label: string; badge?: string }> = ({ active, onClick, label, badge }) => {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-4 py-2 rounded-xl text-xs font-bold border transition-all',
        active
          ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
          : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
      )}
    >
      {label}
      {badge && <span className="ml-1.5 text-accent-400">{badge}</span>}
    </button>
  );
}

function BankPicker({
  title, banks, selected, onToggle,
}: {
  title: string;
  banks: Bank[];
  selected: string[];
  onToggle: (code: string) => void;
}) {
  const list = selected || [];
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-sm font-bold text-white">{title}</span>
        <span className="text-xs text-slate-500">
          {list.length ? `обрано ${list.length}` : 'порожньо'}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {banks.map(bank => (
          <button
            key={bank.code}
            onClick={() => onToggle(bank.code)}
            title={`код ${bank.code}`}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
              list.includes(bank.code)
                ? 'bg-purple-500/10 border-purple-500/30 text-purple-300'
                : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
            )}
          >
            {bank.name}
          </button>
        ))}
      </div>
    </div>
  );
}

function EmptyState({ text, spinner }: { text: string; spinner?: boolean }) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-slate-400 text-sm">
      {spinner && <Loader2 className="w-4 h-4 animate-spin" />}
      {text}
    </div>
  );
}
