import React, { useState } from 'react';
import useSWR from 'swr';
import { Bot, Loader2, ChevronDown, Check } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { useAppStore } from '../../store';
import { ScannerMode, UserFilters } from '../../types';

/**
 * Режим сканера — те, що бот шукає і шле в Telegram.
 *
 * Досі його можна було змінити тільки в розділі «Фільтри», хоча це
 * найчастіша дія: вранці дивишся спреди, вдень ловиш продаж. Тепер він
 * поруч зі списком, який залежить від нього.
 *
 * Не плутати з перемикачем вигляду поруч: той керує тим, що намальовано
 * на екрані, і на поведінку бота не впливає взагалі.
 */

const MODES: { value: ScannerMode; label: string; hint: string }[] = [
  { value: 'SPREAD', label: 'Спред', hint: "Зв'язки купівля→продаж" },
  { value: 'TAKER_BUY', label: 'Taker Buy', hint: 'Полювання на чужі оголошення про продаж' },
  { value: 'TAKER_SELL', label: 'Taker Sell', hint: 'Полювання на чужі оголошення про купівлю' },
  { value: 'MAKER_BUY', label: 'Maker Buy', hint: 'Порада ціни для власного оголошення на купівлю' },
  { value: 'MAKER_SELL', label: 'Maker Sell', hint: 'Порада ціни продажу від ціни закупівлі' },
];

export function ScannerModePicker() {
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const { data: filters, mutate } = useSWR<UserFilters>(
    telegramId ? ['/user/filters', telegramId] : null,
    () => api.getUserFilters(telegramId!),
    { shouldRetryOnError: false }
  );

  // scannerModes зʼявився пізніше — у старих рядках його немає.
  const active: ScannerMode[] =
    filters?.scannerModes ?? (filters?.scannerMode ? [filters.scannerMode] : ['SPREAD']);

  const label = active.length === 1
    ? (MODES.find(m => m.value === active[0])?.label ?? active[0])
    : `${active.length} режими`;

  const toggle = async (mode: ScannerMode) => {
    if (!telegramId) return;

    const next = active.includes(mode)
      ? active.filter(m => m !== mode)
      : [...active, mode];

    if (next.length === 0) {
      toast.warning('Це єдиний активний режим — увімкни інший, щоб зняти цей');
      return;
    }

    setSaving(true);
    // Оптимістично, але без revalidate: інакше на повільній мережі підпис
    // встигне смикнутись назад на старий набір.
    mutate({ ...(filters as UserFilters), scannerModes: next }, false);
    try {
      await api.updateUserFilters(telegramId, { scannerModes: next });
      toast.success(
        active.includes(mode)
          ? `${MODES.find(m => m.value === mode)?.label ?? mode} вимкнено`
          : `${MODES.find(m => m.value === mode)?.label ?? mode} увімкнено`
      );
      await mutate();
    } catch (e: any) {
      await mutate();
      toast.error(`Не змінено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  if (!telegramId) return null;

  return (
    <div className="relative shrink-0">
      <button
        onClick={() => setOpen(v => !v)}
        disabled={saving}
        title={`Бот шукає: ${active.join(', ')}`}
        className={cn(
          'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors disabled:opacity-50 focus:ring-2 focus:ring-accent-500/50 outline-none',
          open
            ? 'bg-slate-800 border-slate-700 text-white'
            : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-white'
        )}
      >
        {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Bot className="w-3.5 h-3.5" />}
        <span className="hidden sm:inline text-slate-500">Бот:</span>
        <span>{label}</span>
        <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute top-full left-0 mt-2 z-50 w-72 bg-slate-900 border border-slate-800 rounded-2xl p-2 shadow-xl">
            <div className="px-3 py-2 text-[10px] uppercase tracking-widest text-slate-600 font-bold">
              Режими сканера
            </div>
            {MODES.map(mode => {
              const on = active.includes(mode.value);
              return (
                <button
                  key={mode.value}
                  onClick={() => toggle(mode.value)}
                  className={cn(
                    'w-full flex items-start gap-2.5 text-left px-3 py-2.5 rounded-xl transition-colors',
                    on ? 'bg-accent-500/10' : 'hover:bg-slate-800'
                  )}
                >
                  <span className={cn(
                    'w-4 h-4 rounded-md border flex items-center justify-center shrink-0 mt-0.5 transition-colors',
                    on ? 'bg-accent-500 border-accent-500' : 'border-slate-700'
                  )}>
                    {on && <Check className="w-3 h-3 text-slate-950" strokeWidth={3} />}
                  </span>
                  <span className="min-w-0">
                    <span className={cn(
                      'block text-sm font-bold',
                      on ? 'text-accent-400' : 'text-slate-200'
                    )}>
                      {mode.label}
                    </span>
                    <span className="block text-[11px] text-slate-500 leading-snug">{mode.hint}</span>
                  </span>
                </button>
              );
            })}
            <p className="px-3 py-2 text-[11px] text-slate-500 leading-snug border-t border-slate-800 mt-1">
              Режимів може бути кілька — бот сканує кожен. Впливає на те, що
              приходить у Telegram; вигляд цієї сторінки задається
              перемикачем ліворуч і від режимів не залежить.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
