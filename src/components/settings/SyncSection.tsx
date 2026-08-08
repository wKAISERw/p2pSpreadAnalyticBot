import React, { useState } from 'react';
import { useSWRConfig } from 'swr';
import { motion } from 'motion/react';
import { RefreshCw, Loader2, Info, Check } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { useAppStore } from '../../store';
import { SyncSectionKey } from '../../types';
import { DEFAULT_SYNC, SYNC_SECTION_LABELS, resolveSync, syncNow } from '../../hooks/useBotSync';

/**
 * Синхронізація з ботом.
 *
 * Тут раніше були кнопки «Забрати з бота» / «Відправити в бот» і три поля:
 * капітал, спред, банки. Виглядало це як обмін даними між двома системами,
 * хоча системи одна: фільтри, картки, ліміти й пресети лежать у базі бота,
 * і сайт читає їх напряму. «Забрати» клало значення в локальні
 * userSettings.maxCapital і userSettings.banks — змінні, яких більше ніхто
 * не читав, тобто обмін нікуди не вів.
 *
 * Справжня проблема була інша: відкрита вкладка не помічала змін, зроблених
 * у Telegram, поки її не перезавантажиш. Саме цим тут тепер і керують.
 */

const INTERVALS = [10, 30, 60, 300];

export default function SyncSection() {
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const userSettings = useAppStore(state => state.userSettings);
  const setUserSettings = useAppStore(state => state.setUserSettings);
  const { mutate } = useSWRConfig();

  const sync = resolveSync(userSettings.sync);
  const [busy, setBusy] = useState(false);

  const patch = (next: Partial<typeof sync>) =>
    setUserSettings({ ...userSettings, sync: { ...sync, ...next } });

  const toggleSection = (key: SyncSectionKey) =>
    patch({ sections: { ...sync.sections, [key]: !sync.sections[key] } });

  const setAll = (value: boolean) =>
    patch({
      sections: Object.fromEntries(
        (Object.keys(sync.sections) as SyncSectionKey[]).map(k => [k, value])
      ) as typeof sync.sections,
    });

  const refreshNow = async () => {
    setBusy(true);
    try {
      await syncNow(sync, mutate);
      toast.success('Дані перечитано з бота');
    } catch (e: any) {
      toast.error(`Не вдалось оновити: ${e?.message ?? 'помилка'}`);
    } finally {
      setBusy(false);
    }
  };

  if (!telegramId) return null;

  const sections = Object.keys(SYNC_SECTION_LABELS) as SyncSectionKey[];
  const activeCount = sections.filter(k => sync.sections[k]).length;

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <RefreshCw className={cn('w-5 h-5 text-blue-400', busy && 'animate-spin')} />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Синхронізація з ботом</h2>
            <p className="text-xs text-slate-400">
              Що сайт перечитує сам, коли ти міняєш це в Telegram
            </p>
          </div>
        </div>

        <button
          onClick={refreshNow}
          disabled={busy || !activeCount}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 text-xs font-bold transition-colors shrink-0"
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Оновити зараз
        </button>
      </div>

      {/* Головний вимикач і частота */}
      <div className="bg-slate-950/60 border border-slate-800 rounded-2xl p-4 mb-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <button
            onClick={() => patch({ enabled: !sync.enabled })}
            className="flex items-center gap-3 text-left"
          >
            <span
              className={cn(
                'w-11 h-6 rounded-full relative transition-colors shrink-0',
                sync.enabled ? 'bg-accent-500' : 'bg-slate-700'
              )}
            >
              <span
                className={cn(
                  'absolute top-0.5 w-5 h-5 rounded-full bg-white transition-all',
                  sync.enabled ? 'right-0.5' : 'left-0.5'
                )}
              />
            </span>
            <span>
              <span className={cn('block text-sm font-bold', sync.enabled ? 'text-accent-400' : 'text-slate-400')}>
                {sync.enabled ? 'Автооновлення увімкнено' : 'Автооновлення вимкнено'}
              </span>
              <span className="block text-[11px] text-slate-500">
                {sync.enabled
                  ? `Кожні ${sync.intervalSeconds} с для ${activeCount} розділів`
                  : 'Дані оновляться при перезаході на сторінку або кнопкою вище'}
              </span>
            </span>
          </button>

          <div className="flex items-center gap-1 bg-slate-950 border border-slate-800 rounded-xl p-1">
            {INTERVALS.map(seconds => (
              <button
                key={seconds}
                onClick={() => patch({ intervalSeconds: seconds })}
                disabled={!sync.enabled}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors disabled:opacity-40',
                  sync.intervalSeconds === seconds
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-500 hover:text-slate-300'
                )}
              >
                {seconds < 60 ? `${seconds} с` : `${seconds / 60} хв`}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Розділи */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
          Що оновлювати
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setAll(true)}
            className="text-[11px] font-bold text-slate-400 hover:text-white transition-colors"
          >
            усі
          </button>
          <span className="text-slate-700">·</span>
          <button
            onClick={() => setAll(false)}
            className="text-[11px] font-bold text-slate-400 hover:text-white transition-colors"
          >
            жодного
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {sections.map(key => {
          const on = sync.sections[key];
          const { title, hint } = SYNC_SECTION_LABELS[key];
          return (
            <button
              key={key}
              onClick={() => toggleSection(key)}
              disabled={!sync.enabled}
              className={cn(
                'flex items-start gap-3 p-4 rounded-2xl border text-left transition-all disabled:opacity-50',
                on ? 'bg-accent-500/5 border-accent-500/25' : 'bg-slate-950/50 border-slate-800'
              )}
            >
              <span
                className={cn(
                  'w-5 h-5 rounded-md border flex items-center justify-center shrink-0 mt-0.5 transition-colors',
                  on ? 'bg-accent-500 border-accent-500' : 'border-slate-700'
                )}
              >
                {on && <Check className="w-3.5 h-3.5 text-slate-950" strokeWidth={3} />}
              </span>
              <span className="min-w-0">
                <span className={cn('block text-sm font-bold', on ? 'text-accent-400' : 'text-slate-300')}>
                  {title}
                </span>
                <span className="block text-[11px] text-slate-500 leading-snug">{hint}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="flex items-start gap-2 mt-4 text-[11px] text-slate-500 leading-snug">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Копіювання тут не відбувається: фільтри, картки, ліміти й пресети
          зберігаються в одному місці — базі бота, — тож сайт і Telegram
          завжди показують те саме. Ці перемикачі керують лише тим, як швидко
          відкрита вкладка помітить зміну. Вимикати варто розділ, який ти
          саме зараз редагуєш на сайті: інакше незбережену чернетку може
          перебити значення з бота.
        </span>
      </div>
    </motion.section>
  );
}
