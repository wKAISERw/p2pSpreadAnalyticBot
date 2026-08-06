import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { Landmark, Loader2, Info } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { Bank, BankLimits, LimitField } from '../../types';

/**
 * Глобальні ліміти по банках. Ліміти конкретної картки перебивають ці —
 * пріоритет такий: override картки → ліміт банку → хардкодний дефолт.
 */

const FIELDS: { key: LimitField; label: string; step: number }[] = [
  { key: 'daily_out_max', label: 'Витрати за добу (₴)', step: 1000 },
  { key: 'daily_in_max', label: 'Надходження за добу (₴)', step: 1000 },
  { key: 'monthly_out_max', label: 'Витрати за місяць (₴)', step: 5000 },
  { key: 'monthly_in_max', label: 'Надходження за місяць (₴)', step: 5000 },
  { key: 'max_single_tx_out', label: 'Макс. одна витрата (₴)', step: 500 },
  { key: 'max_single_tx_in', label: 'Макс. одне надходження (₴)', step: 500 },
  { key: 'max_tx_per_day', label: 'Транзакцій за добу', step: 1 },
  { key: 'cooldown_hours', label: 'Пауза після ліміту (год)', step: 1 },
];

/** snake_case поля з бекенда приходять камелізованими. */
const camel = (key: string) =>
  key.replace(/_([a-z])/g, (_, c) => c.toUpperCase()) as keyof BankLimits;

export default function BankLimitsSection() {
  const { data: limits = [], mutate, isLoading } = useSWR<BankLimits[]>(
    '/user/bank-limits',
    () => api.getBankLimits(),
    { shouldRetryOnError: false }
  );
  const { data: banks = [] } = useSWR<Bank[]>('/banks', () => api.getBanks(), {
    shouldRetryOnError: false,
  });

  const [bank, setBank] = useState('');
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);

  // Банк тут — це назва в БД (monobank, privatbank), а не код зі списку
  // банків біржі, тому беремо з уже збережених лімітів або з довідника.
  const knownBanks = Array.from(
    new Set([...limits.map(l => l.bankName), ...banks.map(b => b.name.toLowerCase())])
  ).sort();

  useEffect(() => {
    if (!bank && knownBanks.length) setBank(knownBanks[0]);
  }, [knownBanks, bank]);

  useEffect(() => { setDraft({}); }, [bank]);

  const stored = limits.find(l => l.bankName === bank);
  const value = (key: LimitField) =>
    draft[key] !== undefined ? draft[key] : Number(stored?.[camel(key)] ?? 0);

  const hasChanges = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await api.setBankLimits(bank, draft);
      toast.success(`Ліміти ${bank} збережено`);
      setDraft({});
      await mutate();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <Landmark className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Ліміти банків</h2>
            <p className="text-xs text-slate-400">Застосовуються до всіх карток цього банку</p>
          </div>
        </div>

        <button
          onClick={save}
          disabled={!hasChanges || saving || !bank}
          className="flex items-center gap-2 px-5 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-all"
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Зберегти
        </button>
      </div>

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Завантажую…
        </p>
      ) : !knownBanks.length ? (
        <p className="text-sm text-slate-500">Немає банків — спершу додай картку в боті.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-5">
            {knownBanks.map(name => (
              <button
                key={name}
                onClick={() => setBank(name)}
                className={cn(
                  'px-4 py-2 rounded-xl text-xs font-bold border transition-all capitalize',
                  bank === name
                    ? 'bg-blue-500/10 border-blue-500/30 text-blue-300'
                    : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
                )}
              >
                {name}
                {limits.some(l => l.bankName === name) && (
                  <span className="ml-1.5 text-blue-400">●</span>
                )}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {FIELDS.map(field => (
              <div key={field.key}>
                <div className="text-xs font-bold text-white mb-0.5">{field.label}</div>
                <div className="text-[10px] text-slate-500 font-mono mb-2">{field.key}</div>
                <input
                  type="number"
                  step={field.step}
                  value={value(field.key)}
                  onChange={e =>
                    setDraft(prev => ({ ...prev, [field.key]: parseFloat(e.target.value) || 0 }))
                  }
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-sm font-bold text-white focus:border-accent-500 outline-none tabular-nums"
                />
              </div>
            ))}
          </div>

          <div className="flex items-start gap-1.5 mt-4 text-[11px] text-slate-500 leading-snug">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              Нуль = без обмеження. Окрема картка може мати власні ліміти — вони
              перебивають банківські.
            </span>
          </div>
        </>
      )}
    </motion.section>
  );
}
