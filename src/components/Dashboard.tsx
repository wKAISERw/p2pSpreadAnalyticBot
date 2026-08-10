import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { TrendingUp, ArrowRightLeft, ShieldAlert, ExternalLink, AlertTriangle, Clock, Copy, Maximize2, Minimize2, LayoutList, LayoutGrid, Target, Activity, ArrowUpDown, Layers, SlidersHorizontal, ChevronDown, ChevronUp } from 'lucide-react';
import { ArbitrageOpportunity, CapitalMode, Order, TransferLeg, UserFilters } from '../types';
import { cn } from '../lib/utils';
import { formatDistanceToNow } from 'date-fns';
import { useAppStore } from '../store';
import { toast } from 'sonner';
import CountUp from 'react-countup';
import useSWR from 'swr';
import { api } from '../services/api';

// Modular components
import { ViewToggle } from './dashboard/ViewToggle';
import { ScannerModePicker } from './dashboard/ScannerModePicker';
import { TakerOrdersList } from './dashboard/TakerOrdersList';
import { ExchangeHealth } from './dashboard/ExchangeHealth';
import { RiskPanel } from './RiskPanel';
import { BankChips, useBankChips } from './BankChips';
import { ReadinessNotice } from './dashboard/ReadinessNotice';
import { UsdtInventory } from './dashboard/UsdtInventory';
import MakerWorkspace from './maker/MakerWorkspace';
import { useExchanges } from '../hooks/useExchanges';
import { useSpreadFilters, SortOption } from '../hooks/useSpreadFilters';
import { useChangeFlash } from '../hooks/useChangeFlash';

/** Підпис згорнутої панелі — щоб було видно, на що дивишся. */
const VIEW_TITLES: Record<string, string> = {
  spread: 'Спреди',
  buy: 'Тейкер · купівля',
  sell: 'Тейкер · продаж',
  both: 'Тейкер · обидві',
  maker: 'Мейкер',
};

const BANK_NAMES_MAP: Record<string, string> = {
  "43": "Monobank",
  "14": "PrivatBank",
  "64": "ПУМБ",
  "48": "А-Банк",
  "99": "Ощадбанк",
  "380": "Raiffeisen",
  "328": "Sense",
  "319": "OTP",
  "553": "izibank",
  "transfer": "Global Transfer"
};

const getBankName = (code: string) => BANK_NAMES_MAP[code] || code;

// Ported from bot/formatters.py — RISK_BADGES
const RISK_BADGES: Record<string, string> = {
  "BEHAVIOR_BOTLIKE":    "🤖",
  "EXACT_LIMITS":        "🎯",
  "API_REPLENISH":       "🤖",
  "STATIC_DROP":         "📏",
  "VELOCITY_SPIKE":      "⚡",
  "CROSS_EXCHANGE_BOT":  "👥",
  "FLICKER_RELIST":      "🔄",
  "BLOCK":               "🚫",
  "NEEDS_LLM":           "🔍",
  "EXTERNAL_LINK":       "🔗",
  "TRIANGLE":            "🔺",
  "CASINO":              "🎰",
  "FINCRIME":            "💸",
  "CHARGEBACK":          "↩️",
  "SUSPICIOUS_BIZ":      "⚠️",
  "CHAT_FIRST":          "💬",
  "APPEAL_PRESSURE":     "📢",
  "BADREVIEWS":          "👎",
  "HIGH_RISK_SCORE":     "📊",
  "BLACKLIST":           "⛔",
};

const getRiskEmoji = (flag: string): string => {
  if (!flag) return "";
  const upper = flag.toUpperCase();
  for (const [key, emoji] of Object.entries(RISK_BADGES)) {
    if (upper.includes(key)) return emoji;
  }
  return "⚠️";
};

export default function Dashboard() {
  const [viewMode, setViewMode] = useState<'detailed' | 'compact'>('detailed');
  const [sortBy, setSortBy] = useState<SortOption>('spread');
  
  // Zustand state
  const userSettings = useAppStore(state => state.userSettings);
  const setUserSettings = useAppStore(state => state.setUserSettings);
  const isFocusMode = useAppStore(state => state.isFocusMode);
  const setIsFocusMode = useAppStore(state => state.setIsFocusMode);
  const view = useAppStore(state => state.dashboardView);
  const isAdmin = useAppStore(state => state.auth?.isAdmin ?? false);

  // Data fetching
  const { data: stats, isLoading: isStatsLoading, mutate: mutateStats } =
    useSWR('/stats', () => api.getStats(), { refreshInterval: 5000, shouldRetryOnError: false });
  const { data: opportunities, isLoading: isOppsLoading } =
    useSWR('/opportunities', () => api.getOpportunities(), { refreshInterval: 5000, shouldRetryOnError: false });

  // Робочий капітал живе в боті. Тягнемо той самий /user/filters, що й
  // однойменний розділ — SWR віддасть його з кешу, зайвого запиту не буде.
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const { data: filters } = useSWR<UserFilters>(
    telegramId ? ['/user/filters', telegramId] : null,
    () => api.getUserFilters(telegramId!),
    { shouldRetryOnError: false }
  );

  const isScannerActive = stats?.isScannerActive ?? false;
  const [isTogglingScanner, setIsTogglingScanner] = useState(false);
  const [showTuning, setShowTuning] = useState(false);

  // Панель керування згортається сама, коли гортаєш список — на телефоні
  // вона інакше з'їдає третину екрана рівно тоді, коли треба дивитись
  // ордери. Назад розгортається лише вручну: самовільно вистрибувати
  // назустріч пальцю — гірше, ніж лишитись згорнутою.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (window.innerWidth >= 768) return;

    const onScroll = () => {
      if (window.scrollY > 220) setCollapsed(true);
      else if (window.scrollY < 40) setCollapsed(false);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  const { names: exchangeNames } = useExchanges();

  /**
   * Старт/стоп ядра. Пишемо в is_scanner_active через /settings/global —
   * саме цей ключ scanner.py перечитує на кожному циклі.
   */
  const toggleScanner = async () => {
    setIsTogglingScanner(true);
    try {
      await api.setScannerActive(!isScannerActive);
      await mutateStats();
      toast.success(isScannerActive ? 'Ядро сканера зупинено' : 'Ядро сканера запущено');
    } catch (error: any) {
      toast.error(`Не вдалось перемкнути сканер: ${error?.message ?? 'помилка'}`);
    } finally {
      setIsTogglingScanner(false);
    }
  };
  
  // Use custom hook for filtering (Taker mode)
  const { opportunities: activeOpportunities, hasExclusions } = useSpreadFilters(opportunities, { sortBy });
  const excludedExchanges = useAppStore(state => state.excludedExchanges);
  const toggleExcludedExchange = useAppStore(state => state.toggleExcludedExchange);

  // Hotkeys
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      // Пробіл раніше перемикав локальний прапорець autoTrade і показував
      // "Scanner Started/Paused", хоча зі сканером це не робило нічого.
      // Реальний старт/стоп тепер — кнопка біля індикатора Scanner;
      // вішати на неї пробіл небезпечно: випадкове натискання зупиняє ядро.
      if (e.key === 'Escape') {
        setUserSettings({ ...userSettings, minSpread: 0.5 });
        toast.info('Filters reset');
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [userSettings, setUserSettings]);

  return (
    <div className="space-y-8">
      {!isFocusMode && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              icon={<TrendingUp className="w-5 h-5 text-accent-400" />}
              label="Активні спреди"
              value={activeOpportunities.length}
              subValue={`${stats?.opportunitiesFound ?? 0} знайдено за сесію`}
              isLoading={isOppsLoading}
            />
            <StatCard
              icon={<ArrowRightLeft className="w-5 h-5 text-blue-400" />}
              label="Циклів пройдено"
              value={stats?.cycles ?? 0}
              subValue={`останній цикл ${Math.round(stats?.lastCycleMs ?? 0)} мс`}
              isLoading={isStatsLoading}
            />
            <StatCard
              icon={<ShieldAlert className="w-5 h-5 text-orange-400" />}
              label="Ботів виявлено"
              value={stats?.botsDetectedToday ?? 0}
              subValue={`${stats?.totalScanned ?? 0} ордерів проскановано`}
              isLoading={isStatsLoading}
            />
            <GoalProgressCard
              currentCapital={filters?.capital}
              capitalMode={filters?.capitalMode}
              goalCapital={userSettings.goalCapital}
            />
          </div>

          <ExchangeHealth />
        </>
      )}

      <div className="space-y-4">
        {/*
          Панель керування у два яруси.

          Раніше все — режим, поріг, усі біржі, фільтри, сортування, вигляд,
          фокус — тиснулось в один ряд. На телефоні це була горизонтальна
          стрічка, де потрібне доводилось шукати прокруткою.

          Верхній ярус — те, що потрібне постійно. Налаштування вибірки
          (біржі, сортування, фільтри) сховані під кнопку.
        */}
        {/* Хедер на телефоні `absolute`, тобто при скролі він їде вгору —
            тож прилипати треба майже до самого верху. Раніше тут стояв
            відступ під нерухому шапку, якої немає, і панель зависала
            посеред порожнечі. */}
        <div className="sticky top-1 md:top-0 z-20 bg-slate-900/90 backdrop-blur-md border border-slate-800 rounded-2xl shadow-lg">
          {/* Згорнутий вигляд для телефона.
              Розгорнута панель займає до третини екрана й лишається там на
              весь скрол — тобто саме тоді, коли потрібен список, а не
              керування ним. Тут лишається те, що має бути видно завжди:
              чи живе ядро і на що ти дивишся. */}
          {collapsed ? (
            <button
              onClick={() => setCollapsed(false)}
              className="w-full flex items-center gap-2 px-3 py-2 text-left"
            >
              <span className={cn(
                'w-2 h-2 rounded-full shrink-0',
                isScannerActive ? 'bg-accent-500' : 'bg-slate-600'
              )} />
              <span className="text-xs font-bold text-white">Сканер</span>
              <span className="text-[11px] text-slate-500 truncate">
                {VIEW_TITLES[view] ?? ''}
              </span>
              <ChevronDown className="w-4 h-4 text-slate-500 ml-auto shrink-0" />
            </button>
          ) : (
          // На телефоні контроли йдуть рядками: стан і режим бота зверху,
          // вигляд сторінки під ними на всю ширину. Одним flex-wrap рядом
          // вони різались краєм екрана.
          <div className="p-2.5 md:p-4 flex flex-wrap items-center gap-2 md:gap-3">
            {/* Стан ядра — реальний із /stats, не локальний прапорець */}
            <div className="flex items-center gap-2 shrink-0">
              <motion.span
                className={cn(
                  "w-2.5 h-2.5 rounded-full",
                  isScannerActive ? "bg-accent-500" : "bg-slate-600"
                )}
                animate={isScannerActive ? { opacity: [1, 0.35, 1] } : { opacity: 1 }}
                transition={{ repeat: Infinity, duration: 2, ease: "easeInOut" }}
              />
              <span className="text-sm font-bold text-white">Сканер</span>
              {isAdmin && (
                <button
                  onClick={toggleScanner}
                  disabled={isTogglingScanner}
                  title={isScannerActive ? "Зупинити ядро" : "Запустити ядро"}
                  className={cn(
                    "px-2 py-0.5 rounded-md text-[10px] font-black uppercase tracking-wider border transition-colors disabled:opacity-50 focus:ring-2 focus:ring-accent-500/50 outline-none",
                    isScannerActive
                      ? "bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20"
                      : "bg-accent-500/10 border-accent-500/30 text-accent-400 hover:bg-accent-500/20"
                  )}
                >
                  {isScannerActive ? "Стоп" : "Старт"}
                </button>
              )}
            </div>

            <div className="h-6 w-px bg-slate-800 shrink-0 hidden sm:block" />

            {/* Режим бота і вигляд сторінки — дві різні речі, тому й два
                контроли поруч, а не один. */}
            <ScannerModePicker />

            {/* Вигляд сторінки — на телефоні окремим рядом на всю ширину:
                двоярусний перемикач у спільному ряду ламав розкладку. */}
            <div className="w-full md:w-auto order-last md:order-none">
              <ViewToggle />
            </div>

            {view === "spread" && (
              <>
                <div className="h-6 w-px bg-slate-800 shrink-0 hidden sm:block" />
                {/* Це поріг ПОКАЗУ, окремий від min_spread_pct у «Фільтрах».
                    Два однойменні числа поруч без пояснення читались як одне
                    й те саме, і люди дивувались, чому Telegram шле те, що
                    сховане на сайті. */}
                <div
                  className="flex items-center gap-2 shrink-0"
                  title="Ховає зі списку слабші спреди. Те, що бот шукає і шле в Telegram, задається в розділі «Фільтри»."
                >
                  <span className="text-xs text-slate-400 font-medium uppercase tracking-wider">
                    Показувати від
                  </span>
                  <input
                    type="number"
                    step="0.1"
                    value={userSettings.minSpread}
                    onChange={(e) => setUserSettings({ ...userSettings, minSpread: parseFloat(e.target.value) || 0 })}
                    className="w-16 bg-slate-950 border border-slate-700 rounded-lg px-2 py-1 text-sm text-white focus:ring-2 focus:ring-accent-500/50 outline-none tabular-nums"
                  />
                  <span className="text-xs text-slate-400">%</span>
                </div>
              </>
            )}

            <div className="flex items-center gap-2 ml-auto shrink-0">
              <button
                onClick={() => setShowTuning(!showTuning)}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold border transition-colors focus:ring-2 focus:ring-accent-500/50 outline-none",
                  showTuning || hasExclusions
                    ? "bg-accent-500/10 border-accent-500/30 text-accent-400"
                    : "bg-slate-950 border-slate-800 text-slate-400 hover:text-white"
                )}
                title="Біржі, сортування, фільтри"
              >
                <SlidersHorizontal className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Вибірка</span>
                {hasExclusions && <span className="w-1.5 h-1.5 rounded-full bg-accent-400" />}
              </button>

              <div className="flex bg-slate-950 rounded-lg p-1 border border-slate-800">
                <button
                  onClick={() => setViewMode("detailed")}
                  className={cn("p-1.5 rounded-md transition-colors focus:ring-2 focus:ring-accent-500/50 outline-none", viewMode === "detailed" ? "bg-slate-800 text-white" : "text-slate-500 hover:text-slate-300")}
                  title="Детально"
                >
                  <LayoutGrid className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setViewMode("compact")}
                  className={cn("p-1.5 rounded-md transition-colors focus:ring-2 focus:ring-accent-500/50 outline-none", viewMode === "compact" ? "bg-slate-800 text-white" : "text-slate-500 hover:text-slate-300")}
                  title="Компактно"
                >
                  <LayoutList className="w-4 h-4" />
                </button>
              </div>

              <button
                onClick={() => setIsFocusMode(!isFocusMode)}
                className={cn(
                  "p-2 rounded-lg transition-colors border focus:ring-2 focus:ring-accent-500/50 outline-none",
                  isFocusMode
                    ? "bg-accent-500/10 border-accent-500/30 text-accent-400"
                    : "bg-slate-950 border-slate-800 text-slate-400 hover:text-white"
                )}
                title="Режим фокусу"
              >
                {isFocusMode ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
              </button>

              {/* Тільки на вузькому екрані: на десктопі панель нікому не
                  заважає, і ховати її нема потреби. */}
              <button
                onClick={() => setCollapsed(true)}
                className="md:hidden p-2 rounded-lg border border-slate-800 bg-slate-950 text-slate-400 hover:text-white transition-colors"
                title="Згорнути панель"
              >
                <ChevronUp className="w-4 h-4" />
              </button>
            </div>
          </div>
          )}

          <AnimatePresence initial={false}>
            {showTuning && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
                className="overflow-hidden border-t border-slate-800"
              >
                <div className="p-3 md:p-4 flex flex-wrap items-center gap-x-4 gap-y-3">
                  {/* Клік вимикає біржу. Один список замість колишньої пари
                      «include + exclude», яка стояла тут поруч і робила
                      протилежні речі над різними наборами бірж. */}
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] text-slate-500 uppercase tracking-wider mr-1">Біржі</span>
                    {exchangeNames.map(ex => {
                      const hidden = excludedExchanges.includes(ex);
                      return (
                        <button
                          key={ex}
                          onClick={() => toggleExcludedExchange(ex)}
                          title={hidden ? `${ex}: приховано — клікни, щоб показати` : `${ex}: показується`}
                          className={cn(
                            "px-2 py-1 rounded-lg text-xs font-bold transition-all border focus:ring-2 focus:ring-accent-500/50 outline-none",
                            hidden
                              ? "bg-slate-950 border-slate-800 text-slate-600 line-through"
                              : "bg-accent-500/10 border-accent-500/30 text-accent-400"
                          )}
                        >
                          {ex}
                        </button>
                      );
                    })}
                    {hasExclusions && (
                      <button
                        onClick={() => useAppStore.getState().setExcludedExchanges([])}
                        className="px-2 py-1 rounded-lg text-xs font-bold text-slate-400 hover:text-white transition-colors"
                      >
                        показати всі
                      </button>
                    )}
                  </div>

                  {view === "spread" && (
                    <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-800 rounded-lg p-1">
                      <ArrowUpDown className="w-3.5 h-3.5 text-slate-500 ml-1" />
                      {([
                        ["spread", "спред"], ["profit", "профіт"],
                        ["deal", "обсяг"], ["risk", "ризик"],
                      ] as const).map(([opt, label]) => (
                        <button
                          key={opt}
                          onClick={() => setSortBy(opt)}
                          className={cn(
                            "px-2 py-1 rounded-md text-xs font-bold transition-all",
                            sortBy === opt
                              ? opt === "risk" ? "bg-red-500/20 text-red-400" : "bg-slate-800 text-white"
                              : "text-slate-500 hover:text-slate-300"
                          )}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/*
          Що малюємо, вирішує вигляд сторінки, а не режим бота.
          Спред — зв'язки з /opportunities; купівля/продаж — окремі ордери
          з /taker/orders, яких на дашборді досі не існувало взагалі.
        */}
        {view === 'spread' && (
          <TakerOpportunitiesList
            opportunities={activeOpportunities}
            isLoading={isOppsLoading}
            viewMode={viewMode}
          />
        )}
        {(view === 'buy' || view === 'sell' || view === 'both') && (
          <div className="space-y-4">
            {/* Причина, з якої алертів не буде, лежить у налаштуваннях і
                видна ще до першого циклу — раніше вона з'ясовувалась лише
                мовчанням сканера. */}
            <ReadinessNotice
              mode={view === 'sell' ? 'TAKER_SELL' : view === 'buy' ? 'TAKER_BUY' : undefined}
            />
            {/* Для продажу важливо не «скільки USDT», а «скільки з них
                можна продати прямо зараз» — фандинг проти споту й Earn. */}
            {(view === 'sell' || view === 'both') && <UsdtInventory />}
            <TakerOrdersList side={view} />
          </div>
        )}
        {view === 'maker' && <MakerWorkspace />}
      </div>
    </div>
  );
}

// Taker mode opportunities list
function TakerOpportunitiesList({ 
  opportunities, 
  isLoading, 
  viewMode 
}: { 
  opportunities: ArbitrageOpportunity[];
  isLoading: boolean;
  viewMode: 'detailed' | 'compact';
}) {
  if (isLoading) {
    return (
      <div className="grid gap-4 grid-cols-1">
        {[1, 2, 3].map((i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="bg-slate-900/80 rounded-3xl p-6 border border-slate-800 animate-pulse"
          >
            <div className="h-8 bg-slate-800 rounded-lg w-1/3 mb-6" />
            <div className="grid grid-cols-2 gap-4">
              <div className="h-32 bg-slate-800 rounded-2xl" />
              <div className="h-32 bg-slate-800 rounded-2xl" />
            </div>
          </motion.div>
        ))}
      </div>
    );
  }

  if (opportunities.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="p-12 border-2 border-dashed border-slate-800 rounded-3xl flex flex-col items-center justify-center text-slate-400"
      >
        <div className="w-12 h-12 bg-slate-900 rounded-full flex items-center justify-center mb-4">
          <TrendingUp className="w-6 h-6 opacity-20" />
        </div>
        <p>Шукаю прибуткові зв'язки…</p>
      </motion.div>
    );
  }

  // Ключ — стабільний id зі сканера, а не позиція в масиві. З індексом у
  // ключі кожне перетасування списку виглядало для React як зміна всіх
  // карток одразу: вони перемонтовувались і програвали анімацію заново.
  //
  // Каскад (staggerChildren) теж прибрано: дані оновлюються раз на 5 секунд,
  // і 50 карток, що виїжджають по черзі, займали більше часу, ніж інтервал
  // між оновленнями — список ніколи не встигав завмерти.
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      <div className="grid gap-4 grid-cols-1">
        {opportunities.map((opp) => (
          <OpportunityCard
            key={opp.id ?? `${opp.buyOrder.id}-${opp.sellOrder.id}`}
            opp={opp}
            viewMode={viewMode}
          />
        ))}
      </div>
    </AnimatePresence>
  );
}


function StatCard({ icon, label, value, subValue, trend, trendUp, isLoading }: any) {
  return (
    <div className="bg-slate-900 border border-slate-800 p-5 rounded-3xl relative overflow-hidden">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800 rounded-xl">{icon}</div>
          <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">{label}</span>
        </div>
        {trend && (
          <span className={cn("text-xs font-bold", trendUp ? "text-accent-400" : "text-red-400")}>
            {trend}
          </span>
        )}
      </div>
      {isLoading ? (
        <div className="h-8 bg-slate-800 rounded w-1/2 mb-1 animate-pulse"></div>
      ) : (
        <div className="text-2xl font-bold text-white mb-1 tabular-nums">
          <CountUp end={Number(value)} duration={1} separator="," />
        </div>
      )}
      <div className="text-xs text-slate-400 font-medium uppercase tracking-widest">{subValue}</div>

      <div className="absolute bottom-0 left-0 w-full h-8 opacity-20 pointer-events-none">
        <svg viewBox="0 0 100 20" preserveAspectRatio="none" className="w-full h-full">
          <polyline
            points="0,20 20,15 40,18 60,10 80,12 100,2"
            fill="none"
            stroke={trendUp ? "#10b981" : "#3b82f6"}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>
    </div>
  );
}

/**
 * Робочий капітал і ціль.
 *
 * currentCapital приходить із scanner_users.working_capital — тобто з тієї
 * ж цифри, якою сканер рахує розмір угоди. Раніше сюди підставлявся
 * локальний userSettings.maxCapital із дефолтом 15 000, який ніде не
 * зберігався й ні на що не впливав: картка показувала прогрес до цілі за
 * капіталом, якого не існує.
 *
 * Поруч на екрані живуть ще два числа — скільки грошей на картках і
 * скільки з них піде в одну угоду. Три схожі суми без пояснення читаються
 * як помилка в одній із них, тому джерело підписане явно: у ручному режимі
 * ця цифра з картками не пов'язана взагалі.
 *
 * Ціль лишається річчю сайту: у боті такого поняття немає.
 */
function GoalProgressCard({
  currentCapital, capitalMode, goalCapital,
}: {
  currentCapital?: number;
  capitalMode?: CapitalMode;
  goalCapital?: number;
}) {
  const hasGoal = Boolean(goalCapital && goalCapital > 0);
  const progress = hasGoal && currentCapital
    ? Math.min(100, Math.max(0, (currentCapital / goalCapital!) * 100))
    : 0;

  return (
    <div className="bg-slate-900 border border-slate-800 p-5 rounded-3xl relative overflow-hidden">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800 rounded-xl">
            <Target className="w-5 h-5 text-purple-400" />
          </div>
          <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">
            Робочий капітал
          </span>
        </div>
        {hasGoal && (
          <span className="text-xs font-bold text-purple-400 tabular-nums">
            <CountUp end={progress} decimals={1} duration={1} />%
          </span>
        )}
      </div>

      {currentCapital === undefined ? (
        <div className="text-2xl font-bold text-slate-600 mb-2">—</div>
      ) : (
        <div className="text-2xl font-bold text-white mb-2 tabular-nums">
          <CountUp end={currentCapital} duration={1} separator=" " /> ₴
        </div>
      )}

      {/* Без цього рядка ручна цифра стоїть поруч із сумою на картках і
          читається як та сама величина — а вона з нею не пов'язана. */}
      {capitalMode && (
        <div className="text-[11px] text-slate-500 mb-2 leading-snug">
          {capitalMode === 'auto'
            ? 'Рахується з балансів карток автоматично.'
            : 'Задано вручну — з балансами карток не пов’язано.'}
        </div>
      )}

      {hasGoal ? (
        <>
          <div className="w-full bg-slate-800 rounded-full h-1.5 mb-1">
            <motion.div
              className="bg-purple-500 h-1.5 rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${progress}%` }}
              transition={{ duration: 1, ease: "easeOut" }}
            />
          </div>
          <div className="text-xs text-slate-400 font-medium uppercase tracking-widest text-right tabular-nums">
            Ціль: {goalCapital!.toLocaleString('uk-UA')} ₴
          </div>
        </>
      ) : (
        <div className="text-xs text-slate-500">
          Ціль не задана — це поле в «Налаштуваннях».
        </div>
      )}
    </div>
  );
}

/**
 * Картка спреду.
 *
 * Обгорнута в memo: SWR віддає новий масив кожні 5 секунд, і без цього
 * перемальовувались усі 50 карток, навіть якщо змінилась одна ціна.
 * Порівнюємо за полями, які реально видно на картці.
 */
const OpportunityCardBase: React.FC<{ opp: ArbitrageOpportunity, viewMode: 'detailed'|'compact' }> = ({ opp, viewMode }) => {
  const isHighRisk = (opp.buyOrder.riskScore || 0) >= 50 || (opp.sellOrder.riskScore || 0) >= 50;

  const resolveBanks = useBankChips();
  const routeBanks = resolveBanks([opp.buyBank, opp.sellBank]);

  // Мережа переказу: сканер рахує за найдешевшою, але возити можна тією,
  // якою ти справді користуєшся. Різниця в комісії відома точно, тож
  // перерахунок тут — арифметика, а не оцінка.
  //
  // Зберігається на беку, а не локально: тим самим значенням користується
  // відбір алертів, інакше сайт показував би один спред, а Telegram слав
  // ордери за іншим.
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const { data: filters, mutate: mutateFilters } = useSWR<UserFilters>(
    telegramId ? ['/user/filters', telegramId] : null,
    () => api.getUserFilters(telegramId!),
    { shouldRetryOnError: false, revalidateOnFocus: false }
  );
  const preferred = filters?.preferredNetwork ?? '';

  const pickNetwork = async (network: string) => {
    if (!telegramId) return;
    const next = network === preferred ? '' : network;
    await mutateFilters({ ...(filters as UserFilters), preferredNetwork: next }, false);
    try {
      await api.updateUserFilters(telegramId, { preferredNetwork: next });
      await mutateFilters();
    } catch (e: any) {
      await mutateFilters();
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    }
  };

  const picked = opp.transfer?.options?.find(o => o.network === preferred);
  const extraFeeUah = picked && opp.transfer
    ? picked.feeUah - opp.transfer.feeUah
    : 0;

  const shownProfit = opp.netProfit - extraFeeUah;
  const shownSpread = opp.dealAmount > 0
    ? opp.netSpread - (extraFeeUah / opp.dealAmount) * 100
    : opp.netSpread;

  const ageInSeconds = Math.floor((Date.now() - opp.timestamp) / 1000);
  const initialProgress = Math.max(0, 100 - (ageInSeconds / 60) * 100);

  const handleCopy = (text: string | number, label: string) => {
    navigator.clipboard.writeText(text.toString());
    toast.success(`Copied ${label}!`);
  };

  // Поява нової картки: коротка й та сама, що й у решті інтерфейсу
  // (див. --arbix-rise в index.css). Довший рух читається як гальмування.
  const variants = {
    hidden: { opacity: 0, y: 8 },
    show: { opacity: 1, y: 0 }
  };
  const enter = { duration: 0.18, ease: [0.22, 1, 0.36, 1] as const };

  // Спред у наявній картці міг поїхати між оновленнями, і побачити це
  // було ніяк — цифра просто ставала іншою.
  const spreadShift = useChangeFlash(opp.netSpread);

  if (viewMode === 'compact') {
    return (
      <motion.div
        layout
        variants={variants}
        initial="hidden"
        animate="show"
        exit={{ opacity: 0, scale: 0.95 }}
        transition={enter}
        className={cn(
          "bg-slate-900/80 backdrop-blur-md overflow-hidden transition-all hover:scale-[1.01] hover:shadow-[0_4px_20px_rgb(var(--accent-rgb)/0.12)] border-t border-slate-800/50 shadow-lg relative rounded-xl",
          isHighRisk ? "border border-red-500/30" : "border border-slate-800"
        )}
      >
        <div className="p-3 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 flex-1">
            <div className="flex items-center -space-x-1">
              <ExchangeIcon name={opp.buyOrder.exchange} size="sm" />
              <motion.div
                animate={{ x: [0, 2, 0] }}
                transition={{ repeat: Infinity, duration: 1.5 }}
                className="z-10 bg-slate-900 rounded-full"
              >
                <ArrowRightLeft className="w-3 h-3 text-slate-400" />
              </motion.div>
              <ExchangeIcon name={opp.sellOrder.exchange} size="sm" />
            </div>
            <div className="flex items-center gap-2 text-sm font-bold text-white tabular-nums">
              <span className="cursor-pointer hover:text-accent-400 transition-colors" onClick={() => handleCopy(opp.buyOrder.price, 'Buy Price')}>
                {opp.buyOrder.price.toFixed(2)}
              </span>
              <span className="text-slate-500">→</span>
              <span className="cursor-pointer hover:text-accent-400 transition-colors" onClick={() => handleCopy(opp.sellOrder.price, 'Sell Price')}>
                {opp.sellOrder.price.toFixed(2)}
              </span>
              <div className="text-xs text-slate-400 font-mono uppercase tracking-widest">
                {getBankName(opp.buyBank)} → {getBankName(opp.sellBank)}
              </div>
            </div>
          </div>

          <div className="flex-1 text-center">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-widest cursor-pointer hover:text-white transition-colors tabular-nums" onClick={() => handleCopy(opp.dealAmount, 'Deal Amount')}>
              Deal: {opp.dealAmount.toFixed(0)} ₴
            </span>
            {/* Risk badges compact */}
            {(opp.buyOrder.riskScore || opp.sellOrder.riskScore ||
              (opp.buyOrder as any).risk_score || (opp.sellOrder as any).risk_score) ? (
              <div className="flex items-center justify-center gap-1 mt-1">
                {[opp.buyOrder, opp.sellOrder].map((ord, i) => {
                  const s = ord.riskScore || (ord as any).risk_score || 0;
                  const f = ord.riskFlag || (ord as any).risk_flag || '';
                  if (!s && !f) return null;
                  return (
                    <span key={i} className={cn(
                      "text-[10px] font-bold px-1.5 py-0.5 rounded border tabular-nums",
                      s >= 50 ? "bg-red-500/10 text-red-400 border-red-500/20" : "bg-orange-500/10 text-orange-400 border-orange-500/20"
                    )} title={f}>
                      {getRiskEmoji(f) || '⚠️'} {s}
                    </span>
                  );
                })}
              </div>
            ) : null}
          </div>

          <div className="flex items-center gap-4 justify-end flex-1">
            <div className="text-right">
              <div
                className={cn(
                  'text-lg font-black tabular-nums leading-none transition-colors',
                  spreadShift === 'up' ? 'text-accent-300'
                    : spreadShift === 'down' ? 'text-orange-400'
                    : 'text-accent-400'
                )}
              >
                +{opp.netSpread.toFixed(2)}%
              </div>
              <div className="text-xs font-bold text-slate-300 tabular-nums">{opp.netProfit.toFixed(0)} ₴</div>
            </div>
            <div className="flex gap-1">
              <motion.a whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} href={opp.buyOrder.link} target="_blank" rel="noreferrer" className="p-1.5 bg-slate-800 hover:bg-slate-700 text-white rounded-lg transition-colors">
                <ExternalLink className="w-3 h-3" />
              </motion.a>
              <motion.a whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} href={opp.sellOrder.link} target="_blank" rel="noreferrer" className="p-1.5 bg-accent-500 hover:bg-accent-400 text-slate-950 rounded-lg transition-colors">
                <ExternalLink className="w-3 h-3" />
              </motion.a>
            </div>
          </div>
        </div>
        <div className="h-0.5 w-full bg-slate-800 absolute bottom-0 left-0">
          <motion.div
            className="h-full bg-accent-500"
            initial={{ width: "100%", backgroundColor: "#10b981" }}
            animate={{ width: "0%", backgroundColor: "#ef4444" }}
            transition={{ duration: 60, ease: "linear" }}
          />
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      layout
      variants={variants}
      initial="hidden"
      animate="show"
      exit={{ opacity: 0, scale: 0.95 }}
      transition={enter}
      className={cn(
        "bg-slate-900/80 backdrop-blur-md rounded-3xl overflow-hidden transition-all hover:scale-[1.01] hover:shadow-[0_4px_20px_rgb(var(--accent-rgb)/0.12)] border-t border-slate-800/50 shadow-lg relative",
        isHighRisk ? "border border-red-500/30" : "border border-slate-800"
      )}
    >
      <div className="p-4 md:p-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-4">
            <div className="flex items-center -space-x-2">
              <ExchangeIcon name={opp.buyOrder.exchange} />
              <motion.div
                animate={{ x: [0, 4, 0] }}
                transition={{ repeat: Infinity, duration: 2 }}
                className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center border-2 border-slate-900 z-10"
              >
                <ArrowRightLeft className="w-3 h-3 text-slate-400" />
              </motion.div>
              <ExchangeIcon name={opp.sellOrder.exchange} />
            </div>
            <div>
              <div className="text-sm font-bold text-white flex items-center gap-2">
                {opp.buyOrder.exchange} → {opp.sellOrder.exchange}
                <span className={cn(
                  "text-xs px-2 py-0.5 rounded-full font-black uppercase tracking-tighter",
                  opp.routeType === 'CROSS' ? "bg-purple-500/20 text-purple-400" : "bg-blue-500/20 text-blue-400"
                )}>
                  {opp.routeType}
                </span>
              </div>
              {/* Маршрут показуємо назвами банків, а не кодами: «328 → 43»
                  читала лише людина з мапою кодів у голові. */}
              <div className="text-xs text-slate-400 tracking-wide">
                {routeBanks[0]?.label ?? opp.buyBank}
                {' → '}
                {routeBanks[1]?.label ?? opp.sellBank}
              </div>
            </div>
          </div>

          <div className="text-left md:text-right border-t border-slate-800/50 md:border-none pt-3 md:pt-0">
            <div className="flex items-baseline gap-2 md:justify-end">
              <span
                className={cn(
                  'text-4xl font-black tabular-nums leading-none transition-colors',
                  spreadShift === 'up' ? 'text-accent-300'
                    : spreadShift === 'down' ? 'text-orange-400'
                    : 'text-accent-400'
                )}
              >
                +{shownSpread.toFixed(2)}%
              </span>
              {spreadShift && (
                <span
                  className={cn(
                    'text-xs font-bold',
                    spreadShift === 'up' ? 'text-accent-300' : 'text-orange-400'
                  )}
                  title="Спред змінився з минулого оновлення"
                >
                  {spreadShift === 'up' ? '▲' : '▼'}
                </span>
              )}
            </div>
            <div className="text-base font-bold text-slate-200 tabular-nums mt-1">
              {shownProfit.toFixed(0)} ₴
              <span className="text-xs font-medium text-slate-500 ml-1.5">чистими</span>
            </div>

            {/* «Чистими» без розшифровки — це слово, яке треба брати на
                віру. Комісії вже відняті, тож видно, скільки саме. */}
            {(opp.totalFeeUah ?? 0) > 0 && (
              <div
                className="text-[11px] text-slate-500 tabular-nums mt-0.5"
                title={(opp.fees ?? []).map(f => `${f.label}: ${f.amountUah.toFixed(2)} ₴`).join('\n')}
              >
                комісій на {Math.round((opp.totalFeeUah ?? 0) + extraFeeUah)} ₴
                {extraFeeUah > 0 && (
                  <span className="text-orange-400/80">
                    {' '}· +{Math.round(extraFeeUah)} за {preferred}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Крок переказу стоїть саме між ногами: у цьому місці він і
            відбувається насправді. */}
        <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 md:gap-2 items-stretch">
          <OrderDetails side="BUY" order={opp.buyOrder} onCopy={handleCopy} />
          <TransferStep
            transfer={opp.transfer}
            selected={preferred || (opp.transfer?.network ?? '')}
            // Вибір спільний для всіх карток і для бота: возиш ти однією
            // мережею, а не окремою під кожен спред.
            onSelect={pickNetwork}
          />
          <OrderDetails side="SELL" order={opp.sellOrder} onCopy={handleCopy} />
        </div>
      </div>

      <div className="bg-slate-800/30 px-4 md:px-6 py-4 flex flex-col md:flex-row md:items-center justify-between border-t border-slate-800 gap-4 md:gap-0">
        <div className="flex items-center justify-between md:justify-start gap-4 w-full md:w-auto">
          <div
            className="text-xs font-mono cursor-pointer hover:text-accent-400 transition-colors group flex items-center gap-1 tabular-nums"
            onClick={() => handleCopy(opp.dealAmount, 'Deal Amount')}
          >
            <span className="text-slate-400">DEAL:</span>
            <span className="text-white font-bold group-hover:text-accent-400">{opp.dealAmount.toFixed(0)} ₴</span>
            <Copy className="w-3 h-3 opacity-0 group-hover:opacity-100" />
          </div>
          <div className="text-xs font-mono text-slate-400 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {opp.timestamp
              ? formatDistanceToNow(new Date(opp.timestamp), { addSuffix: true })
              : 'щойно'}
          </div>
        </div>
        <div className="flex gap-2 w-full md:w-auto">
          <motion.a
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.95 }}
            href={opp.buyOrder.link}
            target="_blank"
            rel="noreferrer"
            className="flex-1 md:flex-none justify-center px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-colors flex items-center gap-2 focus:ring-2 focus:ring-slate-500/50 outline-none"
          >
            BUY <ExternalLink className="w-3 h-3" />
          </motion.a>
          <motion.a
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.95 }}
            href={opp.sellOrder.link}
            target="_blank"
            rel="noreferrer"
            className="flex-1 md:flex-none justify-center px-4 py-2 bg-accent-500 hover:bg-accent-400 text-slate-950 text-xs font-bold rounded-xl transition-colors flex items-center gap-2 shadow-lg shadow-accent-500/20 focus:ring-2 focus:ring-accent-500/50 outline-none"
          >
            SELL <ExternalLink className="w-3 h-3" />
          </motion.a>
        </div>
      </div>
      <div className="h-1 w-full bg-slate-800 absolute bottom-0 left-0">
        <motion.div
          className="h-full bg-accent-500"
          initial={{ width: "100%", backgroundColor: "#10b981" }}
          animate={{ width: "0%", backgroundColor: "#ef4444" }}
          transition={{ duration: 60, ease: "linear" }}
        />
      </div>
    </motion.div>
  );
}

const OpportunityCard = React.memo(OpportunityCardBase, (prev, next) =>
  prev.viewMode === next.viewMode &&
  prev.opp.netSpread === next.opp.netSpread &&
  prev.opp.netProfit === next.opp.netProfit &&
  prev.opp.dealAmount === next.opp.dealAmount &&
  prev.opp.buyOrder.price === next.opp.buyOrder.price &&
  prev.opp.sellOrder.price === next.opp.sellOrder.price &&
  prev.opp.buyOrder.riskFlag === next.opp.buyOrder.riskFlag &&
  prev.opp.sellOrder.riskFlag === next.opp.sellOrder.riskFlag
);

/**
 * Що відбувається між купівлею і продажем.
 *
 * На різних біржах монети треба ще перевезти: обрати мережу, дочекатись
 * підтверджень, заплатити комісію. Комісія й раніше сиділа в чистому
 * спреді, але сам крок ніде не показувався — дві ноги стояли поруч так,
 * ніби USDT опиняється на біржі продажу сам собою. А це рівно та дія, яку
 * людина робить руками, і на неї витрачається час угоди.
 *
 * Порожньо, коли обидві ноги на одній біржі: там переказ внутрішній.
 */
function TransferStep({
  transfer, selected, onSelect,
}: {
  transfer?: TransferLeg | null;
  selected: string;
  onSelect: (network: string) => void;
}) {
  const [open, setOpen] = useState(false);

  if (!transfer) {
    return (
      <div className="hidden md:flex items-center justify-center px-1">
        <div className="w-px h-full bg-slate-800" />
      </div>
    );
  }

  const { unroutable, options = [] } = transfer;
  const current = options.find(o => o.network === selected)
    ?? { network: transfer.network, feeUsdt: transfer.feeUsdt, feeUah: transfer.feeUah };
  const canChoose = options.length > 1 && !unroutable;

  return (
    <div className={cn(
      'relative flex md:flex-col items-center justify-center gap-2 px-3 py-2 md:py-4 rounded-2xl border',
      unroutable
        ? 'bg-red-500/10 border-red-500/25'
        : 'bg-slate-950/60 border-slate-800/60'
    )}>
      <ArrowRightLeft className={cn(
        'w-4 h-4 shrink-0 md:rotate-0 rotate-90',
        unroutable ? 'text-red-400' : 'text-slate-500'
      )} />

      {unroutable ? (
        <div className="text-center">
          <div className="text-[10px] font-bold text-red-400 leading-tight">
            немає спільної мережі
          </div>
          <div className="text-[9px] text-red-400/70 leading-tight mt-0.5">
            переказ неможливий
          </div>
        </div>
      ) : (
        <>
          <button
            onClick={() => canChoose && setOpen(v => !v)}
            disabled={!canChoose}
            title={canChoose ? 'Обрати мережу переказу' : undefined}
            className={cn(
              'text-center rounded-lg px-1.5 py-0.5 transition-colors',
              canChoose && 'hover:bg-slate-800/60 cursor-pointer'
            )}
          >
            <div className="text-[10px] font-black text-slate-300 tracking-wide flex items-center gap-0.5">
              {current.network}
              {canChoose && <ChevronDown className="w-2.5 h-2.5 opacity-60" />}
            </div>
            <div className="text-[9px] text-slate-500 tabular-nums leading-tight mt-0.5">
              {current.feeUsdt} ₮
            </div>
          </button>

          {open && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
              <div className="absolute z-50 top-full mt-1 left-1/2 -translate-x-1/2 w-40 bg-slate-900 border border-slate-800 rounded-xl p-1 shadow-xl">
                {options.map(opt => (
                  <button
                    key={opt.network}
                    onClick={() => { onSelect(opt.network); setOpen(false); }}
                    className={cn(
                      'w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg text-[11px] transition-colors',
                      opt.network === current.network
                        ? 'bg-accent-500/10 text-accent-400'
                        : 'text-slate-400 hover:bg-slate-800'
                    )}
                  >
                    <span className="font-bold">{opt.network}</span>
                    <span className="tabular-nums opacity-70">{opt.feeUsdt} ₮</span>
                  </button>
                ))}
                <div className="px-2 py-1.5 text-[9px] text-slate-600 leading-snug border-t border-slate-800 mt-1">
                  Обрана мережа діє і на відбір алертів у Telegram.
                  Найдешевша тут — {transfer.network}.
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

function OrderDetails({ side, order, onCopy }: { side: 'BUY' | 'SELL', order: Order, onCopy: (text: string|number, label: string) => void }) {
  const score = order.riskScore || (order as any).risk_score || 0;
  // Python backend sends snake_case — handle both
  const riskFlag: string = order.riskFlag || (order as any).risk_flag || '';
  const isRisk = score > 0 || (riskFlag && riskFlag !== 'OK');

  return (
     <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
      <div className="flex items-center justify-between mb-3">
        <span className={cn(
          "text-xs font-black px-2 py-0.5 rounded-md tracking-tighter",
          side === 'BUY' ? "bg-blue-500/20 text-blue-400" : "bg-accent-500/20 text-accent-400"
        )}>{side}</span>
        <div className="flex items-center gap-1 text-xs font-mono text-slate-400 tabular-nums">
          <span>{order.orderCount} orders</span>
          <span>•</span>
         <span>{(order.finishRate ?? 0).toFixed(1)}%</span>
        </div>
      </div>

      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-bold text-white truncate max-w-[120px]">{order.merchantName}</div>
        <div
          className="text-lg font-black text-white cursor-pointer hover:text-accent-400 transition-colors group flex items-center gap-1 tabular-nums"
          onClick={() => onCopy(order.price, `${side} Price`)}
        >
          {(order.price ?? 0).toFixed(2)}
          <Copy className="w-4 h-4 opacity-0 group-hover:opacity-100 text-slate-500" />
        </div>
      </div>

      <div
        className="text-xs text-slate-400 mb-3 font-mono cursor-pointer hover:text-white transition-colors group flex items-center gap-1 tabular-nums"
        onClick={() => onCopy(`${order.minLimit} - ${order.maxLimit}`, `${side} Limits`)}
      >
        Limits: {order.minLimit} - {order.maxLimit} ₴
        <Copy className="w-3 h-3 opacity-0 group-hover:opacity-100" />
      </div>

      {/* Які банки приймає ця нога і чи є серед них твій. Без цього
          доводилось відкривати ордер на біржі, щоб зрозуміти, чи взагалі
          зможеш його взяти. */}
      {(order.bankCodes?.length ?? 0) > 0 && (
        <div className="mb-3">
          <BankChips codes={order.bankCodes} size="sm" />
        </div>
      )}

      {isRisk && (
        <div className="mt-3">
          <RiskPanel riskFlag={riskFlag} score={score} />
        </div>
      )}
    </div>
  );
}

function ExchangeIcon({ name, size = 'md' }: { name: string, size?: 'sm' | 'md' }) {
  const colors: Record<string, string> = {
    'Bybit': 'bg-orange-500',
    'OKX': 'bg-white',
    'Binance': 'bg-yellow-400',
    'MEXC': 'bg-blue-500'
  };
  return (
    <div className={cn(
      "rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-slate-950",
      colors[name] || 'bg-slate-700',
      size === 'sm' ? "w-6 h-6 text-[10px]" : "w-8 h-8 text-xs"
    )}>
      {name[0]}
    </div>
  );
}
