import React, { useState, useMemo } from 'react';
import { motion } from 'motion/react';
import useSWR from 'swr';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar,
} from 'recharts';
import {
  TrendingUp, Activity, DollarSign, Target, Radar, Landmark, Building2, Loader2,
  CalendarDays, Clock,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { api, StatsScope } from '../services/api';
import { useAppStore } from '../store';
import { DetailedStats, Heatmap, PeriodStat } from '../types';

const PERIODS = { '7d': 7, '14d': 14, '30d': 30 } as const;
type Period = keyof typeof PERIODS;

const uah = (value: number | undefined) =>
  `${Math.round(value ?? 0).toLocaleString('uk-UA')} ₴`;

/**
 * Аналітика з GET /api/v1/stats/detailed (core/analytics/stats_engine.py).
 *
 * Раніше вся панель малювалась з generateMockData() — випадкових чисел,
 * які зростали щоразу при перемиканні періоду.
 */
export function AnalyticsPanel() {
  const [timeframe, setTimeframe] = useState<Period>('14d');
  const [scope, setScope] = useState<StatsScope>('mine');
  const isAdmin = useAppStore(state => state.auth?.isAdmin ?? false);
  const periodDays = PERIODS[timeframe];

  const { data, error, isLoading } = useSWR<DetailedStats>(
    ['/stats/detailed', periodDays, scope],
    () => api.getDetailedStats(periodDays, scope),
    { refreshInterval: 60000, shouldRetryOnError: false }
  );

  // Бекенд віддає дні у зворотному порядку (ORDER BY date DESC).
  // Для графіків потрібен хронологічний, плюс накопичувальний підсумок.
  const dailySeries = useMemo(() => {
    const daily = [...(data?.daily ?? [])].reverse();
    let cumulative = 0;
    return daily.map((point) => {
      cumulative += point.profit ?? 0;
      return {
        date: new Date(point.date).toLocaleDateString('uk-UA', { month: 'short', day: 'numeric' }),
        profit: Math.round(point.profit ?? 0),
        trades: point.trades ?? 0,
        cumulative: Math.round(cumulative),
      };
    });
  }, [data?.daily]);

  const summary = data?.summary ?? {};
  const proposals = data?.proposals ?? {};
  const hasTrades = (summary.totalTrades ?? 0) > 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Аналітика та PnL</h1>
          <p className="text-sm text-slate-400">
            {scope === 'mine'
              ? 'Твої завершені угоди та пропозиції сканера'
              : 'Зведення по всіх користувачах'}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Розділення «моє / все» повторює меню статистики в боті. До
              появи параметра scope бекенд рахував усіх користувачів разом,
              і кожен бачив у своєму PnL чужі угоди. */}
          {isAdmin && (
            <div className="flex items-center gap-2 bg-slate-900/50 p-1 rounded-lg border border-slate-800/50">
              {([
                ['mine', 'Мої угоди'],
                ['all', 'Усі юзери'],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setScope(value)}
                  className={cn(
                    'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
                    scope === value
                      ? 'bg-slate-800 text-slate-100 shadow-sm'
                      : 'text-slate-400 hover:text-slate-200'
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          <div className="flex items-center gap-2 bg-slate-900/50 p-1 rounded-lg border border-slate-800/50">
            {(Object.keys(PERIODS) as Period[]).map((t) => (
              <button
                key={t}
                onClick={() => setTimeframe(t)}
                className={cn(
                  'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
                  timeframe === t
                    ? 'bg-slate-800 text-slate-100 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                )}
              >
                {t.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-2xl p-4 text-sm text-red-400">
          Не вдалось завантажити аналітику: {(error as Error).message}
        </div>
      )}

      {isLoading && !data && (
        <div className="flex items-center justify-center gap-2 py-16 text-slate-500 text-sm">
          <Loader2 className="w-4 h-4 animate-spin" />
          Читаю статистику з бази…
        </div>
      )}

      {data && (
        <>
          {/* Завершені угоди */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard
              title="Загальний прибуток"
              value={uah(summary.totalProfit)}
              icon={<TrendingUp className="h-5 w-5 text-accent-400" />}
            />
            <StatCard
              title="Завершених угод"
              value={String(summary.totalTrades ?? 0)}
              icon={<Activity className="h-5 w-5 text-purple-400" />}
            />
            <StatCard
              title="Середній чек"
              value={uah(summary.avgProfit)}
              icon={<Target className="h-5 w-5 text-amber-400" />}
            />
            <StatCard
              title="Найкращий день"
              value={summary.bestDay && summary.bestDay !== 'N/A' ? summary.bestDay : '—'}
              icon={<DollarSign className="h-5 w-5 text-blue-400" />}
            />
          </div>

          {!hasTrades && (
            <div className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-4 text-sm text-slate-400">
              За цей період немає завершених угод — графіки нижче порожні.
              Статистика сканера рахується окремо і показана в блоці «Пропозиції сканера».
            </div>
          )}

          {/* Графіки */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="lg:col-span-2 bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
            >
              <SectionHeader icon={<TrendingUp className="w-5 h-5 text-accent-400" />} title="Накопичений прибуток" />
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={dailySeries} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorCapital" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis dataKey="date" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} dy={10} />
                    <YAxis stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} dx={-10} />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', borderRadius: '8px' }}
                      itemStyle={{ color: '#e4e4e7' }}
                      formatter={(value: number) => [uah(value), 'Накопичено']}
                    />
                    <Area
                      type="monotone"
                      dataKey="cumulative"
                      stroke="#10b981"
                      strokeWidth={2}
                      fillOpacity={1}
                      fill="url(#colorCapital)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
              className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
            >
              <SectionHeader icon={<Activity className="w-5 h-5 text-blue-400" />} title="Прибуток по днях" />
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailySeries} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis dataKey="date" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} dy={10} />
                    <Tooltip
                      cursor={{ fill: '#1e293b', opacity: 0.4 }}
                      contentStyle={{ backgroundColor: '#0f172a', borderColor: '#1e293b', borderRadius: '8px' }}
                      formatter={(value: number) => [uah(value), 'Прибуток']}
                    />
                    <Bar dataKey="profit" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </motion.div>
          </div>

          {/* Пропозиції сканера — є навіть коли угод ще нема */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
          >
            <SectionHeader
              icon={<Radar className="w-5 h-5 text-blue-400" />}
              title="Пропозиції сканера"
            />

            {proposals.total ? (
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                <MiniStat label="Знайдено" value={String(proposals.total)} />
                <MiniStat label="Надіслано" value={String(proposals.sent ?? 0)} />
                <MiniStat label="Середній спред" value={`${proposals.avgSpread ?? 0}%`} />
                <MiniStat label="Макс. спред" value={`${proposals.maxSpread ?? 0}%`} accent />
                <MiniStat label="Середній профіт" value={uah(proposals.avgProfit)} />
                <MiniStat label="Потенціал" value={uah(proposals.totalPotentialProfit)} accent />
              </div>
            ) : (
              <p className="text-sm text-slate-500">За цей період сканер не зафіксував пропозицій.</p>
            )}
          </motion.div>

          {/* Топ бірж і банків */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <RankList
              title="Топ бірж за об'ємом"
              icon={<Building2 className="w-4 h-4 text-slate-400" />}
              rows={(data.exchanges ?? []).map((e) => ({
                label: e.exchange,
                primary: uah(e.volumeUah),
                secondary: `${e.trades} угод`,
                weight: e.volumeUah,
              }))}
            />
            <RankList
              title="Топ банків"
              icon={<Landmark className="w-4 h-4 text-slate-400" />}
              rows={(data.banks ?? []).map((b) => ({
                label: b.bank,
                primary: `${b.trades} угод`,
                secondary: uah(b.volumeUah),
                weight: b.trades,
              }))}
            />
          </div>

          {/* Ці два блоки бекенд віддавав від самого початку, а панель їх
              просто не малювала — у боті вони є в меню статистики. */}
          <HeatmapCard heatmap={data.heatmap} />
          <WeeklyCard weekly={data.weekly} />
        </>
      )}
    </div>
  );
}

/** Порядок днів у відповіді бекенда — як їх віддає strftime('%w'). */
const DAY_ORDER = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAY_LABELS: Record<string, string> = {
  Mon: 'Пн', Tue: 'Вт', Wed: 'Ср', Thu: 'Чт', Fri: 'Пт', Sat: 'Сб', Sun: 'Нд',
};

/** У які години яких днів реально закривались угоди. */
function HeatmapCard({ heatmap }: { heatmap?: Heatmap }) {
  const rows = DAY_ORDER.filter((day) => heatmap?.[day]);
  const max = Math.max(
    1,
    ...rows.flatMap((day) => Object.values(heatmap![day] ?? {}))
  );
  const hasAny = rows.some((day) => Object.values(heatmap![day] ?? {}).some((v) => v > 0));

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <SectionHeader
        icon={<Clock className="w-5 h-5 text-orange-400" />}
        title="Активність по годинах"
      />

      {!hasAny ? (
        <p className="text-sm text-slate-500">За цей період угод не було.</p>
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[42rem] space-y-1">
            <div className="flex gap-1 pl-8">
              {Array.from({ length: 24 }, (_, h) => (
                <div key={h} className="flex-1 text-center text-[9px] text-slate-600 tabular-nums">
                  {h % 3 === 0 ? String(h).padStart(2, '0') : ''}
                </div>
              ))}
            </div>

            {rows.map((day) => (
              <div key={day} className="flex items-center gap-1">
                <span className="w-8 text-[11px] text-slate-500 shrink-0">{DAY_LABELS[day] ?? day}</span>
                {Array.from({ length: 24 }, (_, h) => {
                  const key = String(h).padStart(2, '0');
                  const count = heatmap![day]?.[key] ?? 0;
                  return (
                    <div
                      key={h}
                      title={`${DAY_LABELS[day] ?? day}, ${key}:00 — ${count} угод`}
                      className="flex-1 aspect-square rounded-sm min-w-[10px]"
                      style={{
                        // Прозорість, а не окремі кольори: інакше довелось би
                        // вигадувати пороги «багато/мало» там, де їх немає.
                        backgroundColor: count
                          ? `rgb(var(--accent-rgb) / ${0.15 + (count / max) * 0.85})`
                          : 'rgb(39 39 42 / 0.5)',
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </motion.div>
  );
}

/** Будні проти вихідних: чи варто взагалі сидіти в суботу. */
function WeeklyCard({ weekly }: { weekly?: Record<string, PeriodStat> }) {
  const labels: Record<string, string> = { weekday: 'Будні', weekend: 'Вихідні' };
  const rows = Object.entries(weekly ?? {}).filter(([, v]) => v);

  if (!rows.length) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <SectionHeader
        icon={<CalendarDays className="w-5 h-5 text-purple-400" />}
        title="Будні та вихідні"
      />

      <div className="grid grid-cols-2 gap-4">
        {rows.map(([key, stat]) => (
          <div key={key} className="bg-slate-950/50 border border-slate-800/50 rounded-2xl p-4">
            <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-3">
              {labels[key] ?? key}
            </div>
            <div className="space-y-1.5 text-sm">
              <Row label="Угод" value={String(stat.trades ?? 0)} />
              <Row label="Разом" value={uah(stat.totalProfit)} />
              <Row label="Середній чек" value={uah(stat.avgProfit)} />
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-200 tabular-nums font-medium">{value}</span>
    </div>
  );
}

/** Заголовок секції — той самий, що в решті панелей дашборду. */
function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div className="p-2 bg-slate-800/60 rounded-xl">{icon}</div>
      <h2 className="text-lg font-bold text-white">{title}</h2>
    </div>
  );
}

function StatCard({ title, value, icon }: { title: string; value: string; icon: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-5 flex flex-col"
    >
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm font-medium text-slate-400">{title}</span>
        <div className="p-2 bg-slate-800/50 rounded-lg">{icon}</div>
      </div>
      <span className="text-2xl font-bold text-slate-100 tabular-nums mt-auto">{value}</span>
    </motion.div>
  );
}

function MiniStat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-slate-950/50 border border-slate-800/50 rounded-2xl p-4">
      <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">{label}</div>
      <div className={cn('text-lg font-bold tabular-nums', accent ? 'text-accent-400' : 'text-slate-100')}>
        {value}
      </div>
    </div>
  );
}

interface RankRow {
  label: string;
  primary: string;
  secondary: string;
  weight: number;
}

function RankList({ title, icon, rows }: { title: string; icon: React.ReactNode; rows: RankRow[] }) {
  const max = Math.max(...rows.map((r) => r.weight), 1);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <SectionHeader icon={icon} title={title} />

      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">Немає даних за період.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <div key={row.label}>
              <div className="flex items-center justify-between text-sm mb-1.5">
                <span className="font-medium text-slate-200">{row.label}</span>
                <span className="text-slate-400 tabular-nums">
                  {row.primary} <span className="text-slate-600">• {row.secondary}</span>
                </span>
              </div>
              <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent-500/60 rounded-full"
                  style={{ width: `${Math.max(2, (row.weight / max) * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </motion.div>
  );
}
