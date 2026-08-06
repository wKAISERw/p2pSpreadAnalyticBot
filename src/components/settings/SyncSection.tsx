import React, { useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import {
  RefreshCw, ArrowDownToLine, ArrowUpFromLine, Loader2, Info, Link2, Unlink,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { useAppStore } from '../../store';
import { UserFilters } from '../../types';

/**
 * Синхронізація налаштувань сайту з ботом.
 *
 * Навіщо взагалі вибір, якщо дані «одні й ті самі»: бо вони одні лише
 * доти, доки ти цього хочеш. Цілком нормально тримати на сайті інший
 * поріг спреду, ніж у боті — наприклад, у боті ловити все від 0.5%, а на
 * дашборді дивитись тільки на 1.5%+. Тому кожне поле має власну галочку:
 * увімкнена — значення спільне, вимкнена — живе окремо.
 *
 * Напрямок теж явний. «Забрати з бота» і «Відправити в бот» — різні дії
 * з різними наслідками, і зливати їх в одну кнопку «синхронізувати»
 * означає не дати зрозуміти, чиї значення переможуть.
 */

interface Field {
  key: 'capital' | 'minSpread' | 'banks';
  label: string;
  hint: string;
  /** Як дістати значення з фільтрів бота. */
  fromBot: (f: UserFilters) => string;
  /** Як показати локальне значення. */
  fromLocal: (s: ReturnType<typeof useAppStore.getState>['userSettings']) => string;
}

const FIELDS: Field[] = [
  {
    key: 'capital',
    label: 'Капітал',
    hint: 'working_capital у боті ↔ maxCapital на сайті',
    fromBot: f => `${Math.round(f.capital ?? 0).toLocaleString('uk-UA')} ₴`,
    fromLocal: s => `${Math.round(s.maxCapital ?? 0).toLocaleString('uk-UA')} ₴`,
  },
  {
    key: 'minSpread',
    label: 'Мінімальний спред',
    hint: 'min_spread_pct у боті ↔ фільтр дашборду',
    fromBot: f => `${(f.minSpread ?? 0).toFixed(2)}%`,
    fromLocal: s => `${(s.minSpread ?? 0).toFixed(2)}%`,
  },
  {
    key: 'banks',
    label: 'Банки',
    hint: 'bank_codes у боті ↔ список на сайті',
    fromBot: f => (f.bankCodes?.length ? `${f.bankCodes.length} обрано` : 'порожньо'),
    fromLocal: s => (s.banks?.length ? `${s.banks.length} обрано` : 'порожньо'),
  },
];

export default function SyncSection() {
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const userSettings = useAppStore(state => state.userSettings);
  const setUserSettings = useAppStore(state => state.setUserSettings);

  const { data: filters, isLoading, mutate } = useSWR<UserFilters>(
    telegramId ? ['/user/filters', telegramId] : null,
    () => api.getUserFilters(telegramId!),
    { shouldRetryOnError: false }
  );

  const [busy, setBusy] = useState<'pull' | 'push' | null>(null);

  const prefs = userSettings.syncPreferences ?? {
    capital: true,
    spread: true,
    banks: true,
    apiKeys: false,
  };

  // Ключі налаштувань історично називаються інакше за поля — тримаємо
  // мапу в одному місці, щоб не плутатись у двох іменуваннях.
  const prefKey = (key: Field['key']) =>
    key === 'minSpread' ? 'spread' : key === 'capital' ? 'capital' : 'banks';

  const isOn = (key: Field['key']) => Boolean(prefs[prefKey(key) as keyof typeof prefs]);

  const toggle = (key: Field['key']) => {
    const name = prefKey(key) as keyof typeof prefs;
    setUserSettings({
      ...userSettings,
      syncPreferences: { ...prefs, [name]: !prefs[name] },
    });
  };

  const enabled = FIELDS.filter(f => isOn(f.key));

  const pull = async () => {
    if (!filters || !enabled.length) return;
    setBusy('pull');
    try {
      const next = { ...userSettings };
      if (isOn('capital')) next.maxCapital = filters.capital;
      if (isOn('minSpread')) next.minSpread = filters.minSpread;
      if (isOn('banks')) next.banks = filters.bankCodes ?? [];
      setUserSettings(next);
      toast.success(`Забрано з бота: ${enabled.map(f => f.label.toLowerCase()).join(', ')}`);
    } finally {
      setBusy(null);
    }
  };

  const push = async () => {
    if (!telegramId || !enabled.length) return;
    setBusy('push');
    try {
      // Шлемо тільки відмічені поля: бекенд оновлює те, що прийшло, тож
      // невідмічене лишається в боті недоторканим.
      const patch: Record<string, unknown> = {};
      if (isOn('capital')) patch.workingCapital = userSettings.maxCapital;
      if (isOn('minSpread')) patch.minSpreadPct = userSettings.minSpread;
      if (isOn('banks')) patch.bankCodes = userSettings.banks ?? [];

      const result = await api.updateUserFilters(telegramId, patch as never);
      toast.success(`Відправлено в бот: ${result.updated.join(', ')}`);
      await mutate();
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    } finally {
      setBusy(null);
    }
  };

  if (!telegramId) return null;

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <RefreshCw className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Синхронізація з ботом</h2>
            <p className="text-xs text-slate-400">
              Що спільне, а що живе окремо на сайті
            </p>
          </div>
        </div>

        <div className="flex gap-2">
          <button
            onClick={pull}
            disabled={busy !== null || !enabled.length || isLoading}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-200 text-xs font-bold transition-colors"
            title="Перезаписати значення на сайті тим, що в боті"
          >
            {busy === 'pull' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ArrowDownToLine className="w-3.5 h-3.5" />}
            Забрати з бота
          </button>
          <button
            onClick={push}
            disabled={busy !== null || !enabled.length}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold transition-colors"
            title="Перезаписати значення в боті тим, що на сайті"
          >
            {busy === 'push' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ArrowUpFromLine className="w-3.5 h-3.5" />}
            Відправити в бот
          </button>
        </div>
      </div>

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Читаю налаштування бота…
        </p>
      ) : (
        <div className="space-y-2">
          {FIELDS.map(field => {
            const on = isOn(field.key);
            const botValue = filters ? field.fromBot(filters) : '—';
            const localValue = field.fromLocal(userSettings);
            const differs = botValue !== localValue;

            return (
              <div
                key={field.key}
                className={cn(
                  'flex flex-wrap items-center gap-3 px-4 py-3 rounded-2xl border transition-colors',
                  on
                    ? 'bg-slate-950/60 border-slate-800'
                    : 'bg-slate-950/30 border-slate-800/50'
                )}
              >
                <button
                  onClick={() => toggle(field.key)}
                  className={cn(
                    'flex items-center gap-2 shrink-0',
                    on ? 'text-accent-400' : 'text-slate-500'
                  )}
                  title={on ? 'Спільне значення' : 'Живе окремо на сайті'}
                >
                  {on ? <Link2 className="w-4 h-4" /> : <Unlink className="w-4 h-4" />}
                  <span
                    className={cn(
                      'w-9 h-5 rounded-full relative transition-colors',
                      on ? 'bg-accent-500' : 'bg-slate-700'
                    )}
                  >
                    <span
                      className={cn(
                        'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all',
                        on ? 'right-0.5' : 'left-0.5'
                      )}
                    />
                  </span>
                </button>

                <div className="min-w-0 flex-1">
                  <div className="text-sm font-bold text-white">{field.label}</div>
                  <div className="text-[10px] text-slate-500 font-mono">{field.hint}</div>
                </div>

                <div className="flex items-center gap-4 text-xs tabular-nums shrink-0">
                  <div className="text-right">
                    <div className="text-[10px] uppercase tracking-wider text-slate-600">бот</div>
                    <div className="text-slate-300 font-bold">{botValue}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[10px] uppercase tracking-wider text-slate-600">сайт</div>
                    <div
                      className={cn(
                        'font-bold',
                        // Розбіжність підсвічуємо лише для спільних полів:
                        // для відвʼязаних вона очікувана й нормальна.
                        on && differs ? 'text-orange-400' : 'text-slate-300'
                      )}
                    >
                      {localValue}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex items-start gap-2 mt-4 text-[11px] text-slate-500 leading-snug">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Увімкнена галочка означає, що поле бере участь в обміні. Вимкнена —
          значення на сайті живе своїм життям і в бот не потрапляє.
          Помаранчевим підсвічено розбіжність там, де поля мали б збігатись.
        </span>
      </div>
    </motion.section>
  );
}
