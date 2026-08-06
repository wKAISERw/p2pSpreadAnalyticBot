import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { Crosshair, Plus, Trash2, Loader2, Info } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { SniperDirection, SniperRule } from '../../types';

/**
 * Снайпер-правила. Правило пробиває беззвучний режим: якщо спред і об'єм
 * перевищують пороги на потрібній біржі, алерт піде зі звуком навіть під
 * час паузи.
 *
 * Бекенд не приймає правило без жодного порога — таке спрацьовувало б на
 * кожен алерт і робило б паузу безглуздою.
 */
export function SniperRules({ exchangeNames }: { exchangeNames: string[] }) {
  const { data, isLoading, mutate } = useSWR<SniperRule[]>(
    '/user/sniper',
    () => api.getSniperRules(),
    { shouldRetryOnError: false }
  );

  const [rules, setRules] = useState<SniperRule[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => { setRules(data ?? []); }, [data]);

  const save = async () => {
    setSaving(true);
    try {
      const result = await api.updateSniperRules(rules);
      toast.success(result.count ? `Збережено правил: ${result.count}` : 'Правила очищено');
      await mutate();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  const update = (i: number, patch: Partial<SniperRule>) =>
    setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const isDirty = JSON.stringify(rules) !== JSON.stringify(data ?? []);

  return (
    <section className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <Crosshair className="w-5 h-5 text-red-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Снайпер-правила</h2>
            <p className="text-xs text-slate-400">Пробивають беззвучний режим на великих угодах</p>
          </div>
        </div>

        <button
          onClick={save}
          disabled={!isDirty || saving}
          className="flex items-center gap-2 px-5 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-all"
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Зберегти
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Завантажую…
        </div>
      ) : rules.length === 0 ? (
        <p className="text-sm text-slate-500 mb-4">
          Правил немає — під час паузи алертів не буде взагалі.
        </p>
      ) : (
        <div className="space-y-3 mb-4">
          {rules.map((rule, i) => (
            <div
              key={i}
              className="bg-slate-950/50 border border-slate-800/50 rounded-2xl p-4 space-y-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex flex-wrap gap-2">
                  {(['BUY', 'SELL'] as SniperDirection[]).map(dir => (
                    <button
                      key={dir}
                      onClick={() => update(i, { direction: dir })}
                      title={dir === 'BUY'
                        ? 'Стежимо за біржею, де ми продаємо'
                        : 'Стежимо за біржею, де ми купуємо'}
                      className={cn(
                        'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
                        rule.direction === dir
                          ? 'bg-red-500/10 border-red-500/30 text-red-400'
                          : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
                      )}
                    >
                      {dir}
                    </button>
                  ))}
                </div>

                <button
                  onClick={() => setRules(rules.filter((_, j) => j !== i))}
                  className="p-1.5 text-slate-500 hover:text-red-400 transition-colors shrink-0"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {exchangeNames.map(name => (
                  <button
                    key={name}
                    onClick={() => update(i, { exchange: name })}
                    className={cn(
                      'px-2.5 py-1 rounded-lg text-[11px] font-bold border transition-all',
                      rule.exchange === name
                        ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                        : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
                    )}
                  >
                    {name}
                  </button>
                ))}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <LabeledNum
                  label="Мін. спред (%)"
                  value={rule.minSpread}
                  step={0.1}
                  onChange={v => update(i, { minSpread: v })}
                />
                <LabeledNum
                  label="Мін. об'єм (₴)"
                  value={rule.minVolume}
                  step={1000}
                  onChange={v => update(i, { minVolume: v })}
                />
              </div>

              {rule.minSpread <= 0 && rule.minVolume <= 0 && (
                <p className="text-[11px] text-orange-400">
                  Потрібен хоча б один поріг — інакше правило спрацює на кожному алерті.
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={() =>
            setRules([
              ...rules,
              {
                exchange: exchangeNames[0] ?? 'Bybit',
                direction: 'BUY',
                minSpread: 1.5,
                minVolume: 20000,
              },
            ])
          }
          className="flex items-center gap-1.5 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-colors"
        >
          <Plus className="w-3.5 h-3.5" /> Правило
        </button>

        <div className="flex items-start gap-1.5 text-[11px] text-slate-500">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>BUY — біржа, де продаємо. SELL — біржа, де купуємо.</span>
        </div>
      </div>
    </section>
  );
}

function LabeledNum({
  label, value, onChange, step,
}: {
  label: string; value: number; onChange: (v: number) => void; step: number;
}) {
  return (
    <div>
      <div className="text-[11px] text-slate-400 mb-1">{label}</div>
      <input
        type="number"
        step={step}
        value={Number.isFinite(value) ? value : 0}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-bold text-white focus:border-accent-500 outline-none tabular-nums"
      />
    </div>
  );
}
