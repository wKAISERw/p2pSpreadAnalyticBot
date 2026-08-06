import React, { useState, useMemo } from 'react';
import { motion } from 'motion/react';
import useSWR from 'swr';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar,
} from 'recharts';
import { TrendingUp, Activity, DollarSign, Target, Radar, Landmark, Building2, Loader2 } from 'lucide-react';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { DetailedStats } from '../types';

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
  const periodDays = PERIODS[timeframe];

  const { data, error, isLoading } = useSWR<DetailedStats>(
    ['/stats/detailed', periodDays],
    () => api.getDetailedStats(periodDays),
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
          <h1 className="text-2xl font-bold text-zinc-100">Аналітика та PnL</h1>
          <p className="text-sm text-zinc-400">Завершені угоди та пропозиції сканера з бази бота</p>
        </div>

        <div className="flex items-center gap-2 bg-zinc-900/50 p-1 rounded-lg border border-zinc-800/50">
          {(Object.keys(PERIODS) as Period[]).map((t) => (
            <button
              key={t}
              onClick={() => setTimeframe(t)}
              className={cn(
                'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
                timeframe === t
                  ? 'bg-zinc-800 text-zinc-100 shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200'
              )}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-sm text-red-400">
          Не вдалось завантажити аналітику: {(error as Error).message}
        </div>
      )}

      {isLoading && !data && (
        <div className="flex items-center justify-center gap-2 py-16 text-zinc-500 text-sm">
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
            <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-4 text-sm text-zinc-400">
              За цей період немає завершених угод — графіки нижче порожні.
              Статистика сканера рахується окремо і показана в блоці «Пропозиції сканера».
            </div>
          )}

          {/* Графіки */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="lg:col-span-2 bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-6"
            >
              <h3 className="text-sm font-medium text-zinc-400 mb-6">Накопичений прибуток</h3>
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={dailySeries} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="colorCapital" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                    <XAxis dataKey="date" stroke="#52525b" fontSize={12} tickLine={false} axisLine={false} dy={10} />
                    <YAxis stroke="#52525b" fontSize={12} tickLine={false} axisLine={false} dx={-10} />
                    <Tooltip
                      contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', borderRadius: '8px' }}
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
              className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-6"
            >
              <h3 className="text-sm font-medium text-zinc-400 mb-6">Прибуток по днях</h3>
              <div className="h-[300px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={dailySeries} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                    <XAxis dataKey="date" stroke="#52525b" fontSize={12} tickLine={false} axisLine={false} dy={10} />
                    <Tooltip
                      cursor={{ fill: '#27272a', opacity: 0.4 }}
                      contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', borderRadius: '8px' }}
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
            className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-6"
          >
            <div className="flex items-center gap-2 mb-5">
              <Radar className="w-4 h-4 text-zinc-400" />
              <h3 className="text-sm font-medium text-zinc-400">Пропозиції сканера</h3>
            </div>

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
              <p className="text-sm text-zinc-500">За цей період сканер не зафіксував пропозицій.</p>
            )}
          </motion.div>

          {/* Топ бірж і банків */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <RankList
              title="Топ бірж за об'ємом"
              icon={<Building2 className="w-4 h-4 text-zinc-400" />}
              rows={(data.exchanges ?? []).map((e) => ({
                label: e.exchange,
                primary: uah(e.volumeUah),
                secondary: `${e.trades} угод`,
                weight: e.volumeUah,
              }))}
            />
            <RankList
              title="Топ банків"
              icon={<Landmark className="w-4 h-4 text-zinc-400" />}
              rows={(data.banks ?? []).map((b) => ({
                label: b.bank,
                primary: `${b.trades} угод`,
                secondary: uah(b.volumeUah),
                weight: b.trades,
              }))}
            />
          </div>
        </>
      )}
    </div>
  );
}

function StatCard({ title, value, icon }: { title: string; value: string; icon: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5 flex flex-col"
    >
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm font-medium text-zinc-400">{title}</span>
        <div className="p-2 bg-zinc-800/50 rounded-lg">{icon}</div>
      </div>
      <span className="text-2xl font-bold text-zinc-100 tabular-nums mt-auto">{value}</span>
    </motion.div>
  );
}

function MiniStat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-zinc-950/50 border border-zinc-800/50 rounded-lg p-3">
      <div className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1">{label}</div>
      <div className={cn('text-lg font-bold tabular-nums', accent ? 'text-accent-400' : 'text-zinc-100')}>
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
      className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-6"
    >
      <div className="flex items-center gap-2 mb-5">
        {icon}
        <h3 className="text-sm font-medium text-zinc-400">{title}</h3>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-zinc-500">Немає даних за період.</p>
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <div key={row.label}>
              <div className="flex items-center justify-between text-sm mb-1.5">
                <span className="font-medium text-zinc-200">{row.label}</span>
                <span className="text-zinc-400 tabular-nums">
                  {row.primary} <span className="text-zinc-600">• {row.secondary}</span>
                </span>
              </div>
              <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
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
