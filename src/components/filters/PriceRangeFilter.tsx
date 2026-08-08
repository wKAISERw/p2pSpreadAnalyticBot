import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { Loader2, Save } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { PriceRange, PriceRangeMode } from '../../types';

/**
 * Ціновий фільтр входу.
 *
 * Стосується лише спред-режиму: обмежує ціну КУПІВЛІ у зв'язці. Тейкер має
 * власні стратегії (див. TakerSettings), тому дублювати їх тут немає сенсу.
 *
 * У боті це меню існувало давно, але значення нікуди не йшло:
 * PriceRangeFilter не імпортувався в жодному місці конвеєра, тож ордери за
 * ним не відсіювались. На сайті цього налаштування не було взагалі.
 */

const MODES: { value: PriceRangeMode; label: string; hint: string }[] = [
  { value: '', label: 'Вимкнено', hint: 'Ціна входу не обмежується' },
  { value: 'max', label: 'Не дорожче', hint: 'Купувати лише за ціною ≤ вказаної' },
  { value: 'min', label: 'Не дешевше', hint: 'Пропускати все, що дешевше за поріг' },
  { value: 'range', label: 'Діапазон', hint: 'Ціна входу між «від» і «до»' },
  { value: 'exact', label: 'Точна ціна', hint: 'Збіг із допуском ±0.01 ₴' },
];

export function PriceRangeFilter() {
  const { data, mutate } = useSWR<PriceRange>(
    '/user/price-range',
    () => api.getPriceRange(),
    { shouldRetryOnError: false }
  );

  const [draft, setDraft] = useState<PriceRange>({});
  const [saving, setSaving] = useState(false);

  // Чернетка живе поверх серверного значення, поки її не збережуть.
  useEffect(() => { setDraft(data ?? {}); }, [data]);

  const mode = (draft.mode ?? '') as PriceRangeMode;
  const dirty = JSON.stringify(draft) !== JSON.stringify(data ?? {});

  const save = async () => {
    setSaving(true);
    try {
      await api.setPriceRange(draft);
      toast.success(mode ? 'Ціновий фільтр збережено' : 'Ціновий фільтр вимкнено');
      await mutate();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="pt-5 mt-5 border-t border-slate-800/70">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-sm font-bold text-white">Ціна входу</div>
          <div className="text-[11px] text-slate-500">
            Обмежує ціну купівлі у зв'язці · price_range_json
          </div>
        </div>

        <button
          onClick={save}
          disabled={!dirty || saving}
          className="flex items-center gap-1.5 px-4 py-1.5 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-lg transition-all shrink-0"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          Зберегти
        </button>
      </div>

      <div className="flex flex-wrap gap-2 mb-3">
        {MODES.map(m => (
          <button
            key={m.value || 'off'}
            onClick={() => setDraft({ mode: m.value })}
            title={m.hint}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
              mode === m.value
                ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
            )}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === 'range' && (
        <div className="grid grid-cols-2 gap-3">
          <Num
            label="Від (₴)"
            value={draft.min ?? 0}
            onChange={v => setDraft({ ...draft, min: v })}
          />
          <Num
            label="До (₴)"
            value={draft.max ?? 0}
            onChange={v => setDraft({ ...draft, max: v })}
          />
        </div>
      )}

      {(mode === 'max' || mode === 'min' || mode === 'exact') && (
        <Num
          label={
            mode === 'max' ? 'Не дорожче ніж (₴)'
              : mode === 'min' ? 'Не дешевше ніж (₴)'
              : 'Точна ціна (₴)'
          }
          value={draft.value ?? 0}
          onChange={v => setDraft({ ...draft, value: v })}
        />
      )}

      {mode === '' && (
        <p className="text-[11px] text-slate-500">
          Зв'язки надсилаються за будь-якою ціною входу — обмежують лише
          спред і капітал.
        </p>
      )}
    </div>
  );
}

function Num({
  label, value, onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="text-[11px] text-slate-400 mb-1.5">{label}</div>
      <input
        type="number"
        step={0.01}
        value={Number.isFinite(value) ? value : 0}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm font-bold text-white focus:border-accent-500 outline-none transition-all tabular-nums"
      />
    </div>
  );
}
