import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { useSearchParams } from 'react-router-dom';
import { SlidersHorizontal, Wallet, Percent, Building2, Users, Loader2, Save, Bell, BellOff, Check, Crosshair, UserCheck, Target } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { useExchanges } from '../hooks/useExchanges';
import { TakerSettings } from './filters/TakerSettings';
import { MerchantThresholds } from './filters/MerchantThresholds';
import { SniperRules } from './filters/SniperRules';
import { PriceRangeFilter } from './filters/PriceRangeFilter';
import { BankScopes } from './filters/BankScopes';
import {
  Bank, ScannerMode, SpreadStrategy, CapitalMode, UserFilters, UserFiltersPatch,
} from '../types';
import LoadingVeil from './LoadingVeil';

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
export type FilterSectionId =
  | 'modes' | 'taker' | 'capital' | 'spread' | 'banks' | 'merchant' | 'sniper';

const FILTER_SECTION_IDS: FilterSectionId[] = [
  'modes', 'taker', 'capital', 'spread', 'banks', 'merchant', 'sniper',
];

/** Перший показ — усе розгорнуте, як було до появи вибору розділів. */
const DEFAULT_FILTER_SECTIONS = FILTER_SECTION_IDS;

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

  // Які розділи розгорнуті. В URL, а не в стані компонента: посилання на
  // «спред + банки» має відкриватись тим самим, і перезавантаження не
  // повинно скидати розкладку.
  const [searchParams, setSearchParams] = useSearchParams();
  const visible = React.useMemo<Set<FilterSectionId>>(() => {
    const raw = searchParams.get('s');
    if (raw === null) return new Set(DEFAULT_FILTER_SECTIONS);
    const ids = raw.split(',').filter(Boolean) as FilterSectionId[];
    return new Set(ids.filter(id => FILTER_SECTION_IDS.includes(id)));
  }, [searchParams]);

  const toggleSection = (id: FilterSectionId, only = false) => {
    // Подвійний клік лишає один розділ: коли треба зосередитись на
    // чомусь одному, знімати решту по черзі — зайва робота.
    const next = only
      ? new Set<FilterSectionId>([id])
      : new Set(visible);
    if (!only) {
      if (next.has(id)) next.delete(id);
      else next.add(id);
    }
    setSearchParams({ s: [...next].join(',') }, { replace: true });
  };

  // Чернетка живе поверх завантажених фільтрів: показуємо draft ?? server.
  useEffect(() => { setDraft({}); }, [filters?.userId]);

  if (!telegramId) return <EmptyState text="Потрібен вхід через Telegram." />;
  if (isLoading) return <LoadingVeil compact label="Читаю фільтри з бота" />;
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

  // Набір активних режимів. scannerModes зʼявився пізніше за scannerMode,
  // тож у старих рядках його немає — падаємо на одиничний режим.
  const activeModes: ScannerMode[] =
    (value('scannerModes', filters.scannerModes) as ScannerMode[] | undefined)
    ?? (filters.scannerMode ? [filters.scannerMode] : ['SPREAD']);

  const toggleMode = (mode: ScannerMode) => {
    const next = activeModes.includes(mode)
      ? activeModes.filter(m => m !== mode)
      : [...activeModes, mode];
    if (next.length === 0) return;
    set('scannerModes', next);
  };

  /** Який пресет тейкера показувати першим — беремо перший увімкнений. */
  const activeMode = activeModes.find(m => m !== 'SPREAD') ?? activeModes[0];
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

      <SectionPicker visible={visible} toggle={toggleSection} />

      {/* Режими сканера */}
      {visible.has('modes') && (
      <Section icon={<SlidersHorizontal className="w-5 h-5 text-accent-400" />} title="Режими сканера">
        <p className="text-xs text-slate-400 -mt-2 mb-4">
          Можна тримати кілька одночасно — наприклад, і купівлю, і продаж.
          Кожен режим працює за своїм набором налаштувань нижче.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {SCANNER_MODES.map(mode => {
            const active = activeModes.includes(mode.value);
            // Останній режим не даємо зняти: користувач без жодного нічого
            // не отримує, і це читається як поламаний бот. Для тиші є
            // окремий перемикач алертів у шапці.
            const isLast = active && activeModes.length === 1;
            return (
              <button
                key={mode.value}
                onClick={() => toggleMode(mode.value)}
                disabled={isLast}
                title={isLast ? 'Це єдиний активний режим' : mode.hint}
                className={cn(
                  'text-left p-4 rounded-2xl border transition-all disabled:cursor-not-allowed',
                  active
                    ? 'bg-accent-500/10 border-accent-500/30'
                    : 'bg-slate-950/50 border-slate-800 hover:border-slate-700'
                )}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className={cn(
                    'w-4 h-4 rounded-md border flex items-center justify-center shrink-0 transition-colors',
                    active ? 'bg-accent-500 border-accent-500' : 'border-slate-700'
                  )}>
                    {active && <Check className="w-3 h-3 text-slate-950" strokeWidth={3} />}
                  </span>
                  <span className={cn('font-bold text-sm', active ? 'text-accent-400' : 'text-white')}>
                    {mode.label}
                  </span>
                </div>
                <div className="text-xs text-slate-400 leading-snug">{mode.hint}</div>
              </button>
            );
          })}
        </div>
      </Section>
      )}

      {/*
        Тейкер-налаштування.

        Раніше блок з'являвся лише тоді, коли обраний відповідний режим:
        подивитись, що виставлено в Taker Sell, сидячи в Taker Buy, було
        неможливо — доводилось перемикати режим сканера (і мимоволі його
        зберігати). Тепер обидва набори доступні завжди, з окремою вкладкою;
        активний режим просто підсвічений.

        Колонки в базі різні (taker_buy_* і taker_sell_*), тож редагування
        неактивного набору нічого не ламає — воно просто чекає свого режиму.
      */}
      {visible.has('taker') && (
        <TakerWorkspace
          activeMode={activeMode}
          filters={filters as any}
          value={value}
          set={set}
          exchangeNames={exchangeNames}
        />
      )}

      {/* Капітал і спред */}
      {(visible.has('capital') || visible.has('spread')) && (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {visible.has('capital') && (
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
        )}

        {visible.has('spread') && activeModes.includes('SPREAD') && (
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

          {/* Ціновий фільтр входу. Зберігається окремим ендпоінтом, бо це
              структура, а не скаляр — тому й кнопка збереження власна. */}
          <PriceRangeFilter />
        </Section>
        )}
      </div>
      )}

      {/* Банки */}
      {visible.has('banks') && (
      <Section icon={<Building2 className="w-5 h-5 text-purple-400" />} title="Банки">
        <BankScopes
          banks={banks}
          activeModes={activeModes}
          sharedValue={side => {
            if (side === 'general') return value('bankCodes', filters.bankCodes);
            if (side === 'buy') return value('buyBankCodes', filters.buyBankCodes);
            return value('sellBankCodes', filters.sellBankCodes);
          }}
          onToggleShared={(field, code) => {
            const current =
              field === 'bankCodes' ? value('bankCodes', filters.bankCodes)
                : field === 'buyBankCodes' ? value('buyBankCodes', filters.buyBankCodes)
                : value('sellBankCodes', filters.sellBankCodes);
            toggleBank(field, code, current);
          }}
        />
      </Section>
      )}

      {visible.has('merchant') && (
        <MerchantThresholds exchangeNames={exchangeNames} onSaved={mutate} />
      )}

      {visible.has('sniper') && <SniperRules exchangeNames={exchangeNames} />}

      {visible.size === 0 && (
        <div className="p-8 border-2 border-dashed border-slate-800 rounded-2xl text-center text-sm text-slate-500">
          Усі розділи сховані — вибери, що показати, кнопками вище.
        </div>
      )}
    </div>
  );
}

/**
 * Які розділи фільтрів показувати.
 *
 * Тут не вкладки: розділів сім, і майже завжди правиш два-три пов'язані
 * між собою — спред разом із банками, тейкер разом із капіталом. Вкладка
 * змусила б стрибати туди-сюди й тримати попередній екран у голові, а
 * суцільна стрічка — це кілька екранів прокрутки без орієнтиру.
 *
 * Вибір лежить в URL: посилання на «спред + банки» можна відкрити знову й
 * побачити те саме, а перезавантаження не скидає розкладку.
 */
const FILTER_SECTIONS: { id: FilterSectionId; label: string; icon: React.ElementType }[] = [
  { id: 'modes', label: 'Режими', icon: SlidersHorizontal },
  { id: 'taker', label: 'Тейкер', icon: Crosshair },
  { id: 'capital', label: 'Капітал', icon: Wallet },
  { id: 'spread', label: 'Спред', icon: Percent },
  { id: 'banks', label: 'Банки', icon: Building2 },
  { id: 'merchant', label: 'Мерчанти', icon: UserCheck },
  { id: 'sniper', label: 'Снайпер', icon: Target },
];

function SectionPicker({
  visible, toggle,
}: {
  visible: Set<FilterSectionId>;
  toggle: (id: FilterSectionId, only?: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {FILTER_SECTIONS.map(({ id, label, icon: Icon }) => {
        const on = visible.has(id);
        return (
          <button
            key={id}
            onClick={() => toggle(id)}
            onDoubleClick={() => toggle(id, true)}
            title={on ? 'Прибрати розділ' : 'Показати розділ'}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold border transition-colors',
              on
                ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                : 'bg-slate-900/50 border-slate-800 text-slate-500 hover:text-slate-300'
            )}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Обидва тейкер-набори з перемикачем.
 *
 * За замовчуванням відкритий той, що відповідає активному режиму сканера —
 * але піти подивитись сусідній можна без наслідків.
 */
function TakerWorkspace({
  activeMode, filters, value, set, exchangeNames,
}: {
  activeMode: ScannerMode;
  filters: UserFilters & Record<string, any>;
  value: <K extends keyof UserFiltersPatch>(key: K, fallback: any) => any;
  set: <K extends keyof UserFiltersPatch>(key: K, v: UserFiltersPatch[K]) => void;
  exchangeNames: string[];
}) {
  type TakerMode = 'TAKER_BUY' | 'TAKER_SELL';
  const isTakerActive = activeMode === 'TAKER_BUY' || activeMode === 'TAKER_SELL';
  const [tab, setTab] = useState<TakerMode>(
    isTakerActive ? (activeMode as TakerMode) : 'TAKER_BUY'
  );

  // Перемкнув режим сканера — показуємо відповідний набір. Але якщо людина
  // сама відкрила іншу вкладку, не смикаємо її назад на кожен рендер.
  useEffect(() => {
    if (isTakerActive) setTab(activeMode as TakerMode);
  }, [activeMode, isTakerActive]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 bg-slate-950 border border-slate-800 rounded-xl p-1">
          {([
            ['TAKER_BUY', 'Taker Buy'],
            ['TAKER_SELL', 'Taker Sell'],
          ] as const).map(([mode, label]) => (
            <button
              key={mode}
              onClick={() => setTab(mode)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-bold transition-colors',
                tab === mode ? 'bg-slate-800 text-white' : 'text-slate-500 hover:text-slate-300'
              )}
            >
              {label}
              {activeMode === mode && <span className="ml-1.5 text-accent-400">●</span>}
            </button>
          ))}
        </div>

        <span className="text-[11px] text-slate-500">
          {activeMode === tab
            ? 'Активний режим сканера'
            : isTakerActive
              ? 'Не активний зараз — зміни збережуться і чекатимуть свого режиму'
              : `Зараз працює режим ${activeMode} — ці налаштування не читаються`}
        </span>
      </div>

      <TakerSettings
        mode={tab}
        filters={filters}
        value={value}
        set={set}
        exchangeNames={exchangeNames}
      />
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

function EmptyState({ text, spinner }: { text: string; spinner?: boolean }) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-slate-400 text-sm">
      {spinner && <Loader2 className="w-4 h-4 animate-spin" />}
      {text}
    </div>
  );
}
