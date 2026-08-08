import React, { useMemo, useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import {
  AlertTriangle, CheckCircle2, XCircle, Clock, Activity, Download, Search,
} from 'lucide-react';
import { LogRecord } from '../types';
import { cn } from '../lib/utils';
import { api } from '../services/api';

/**
 * Лог сканера з GET /api/v1/logs.
 *
 * Раніше цей розділ називався «Auto-Trading Engine» і мав кнопки START/STOP,
 * Max Trade Amount, Min Spread, Max Risk Score та вибір бірж. Жодне з цих
 * полів нікуди не йшло: вони писались у localStorage і не читались навіть
 * усередині сайту, не кажучи про бота. Виконанням угод керує trade_worker,
 * HTTP-ендпоінта під нього немає — тож пульт лише вдавав керування.
 *
 * Лишилось те, що тут справді працює: перегляд, фільтр і вивантаження логу.
 */

const LEVEL_STYLES: Record<string, { color: string; badge: string; icon: React.ReactNode }> = {
  CRITICAL: { color: 'text-red-500', badge: 'bg-red-500/20 text-red-400', icon: <XCircle className="w-4 h-4" /> },
  ERROR: { color: 'text-red-500', badge: 'bg-red-500/10 text-red-400', icon: <XCircle className="w-4 h-4" /> },
  WARNING: { color: 'text-orange-400', badge: 'bg-orange-500/10 text-orange-400', icon: <AlertTriangle className="w-4 h-4" /> },
  INFO: { color: 'text-blue-400', badge: 'bg-blue-500/10 text-blue-400', icon: <CheckCircle2 className="w-4 h-4" /> },
  DEBUG: { color: 'text-slate-500', badge: 'bg-slate-800 text-slate-400', icon: <Activity className="w-4 h-4" /> },
};

type LevelFilter = 'ALL' | 'WARNING' | 'ERROR';

export default function LogsPanel() {
  const { data: logs = [], isLoading } = useSWR<LogRecord[]>('/logs', () => api.getLogs(200), {
    refreshInterval: 5000,
    shouldRetryOnError: false,
  });

  const [levelFilter, setLevelFilter] = useState<LevelFilter>('ALL');
  const [query, setQuery] = useState('');

  const visibleLogs = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return logs.filter(log => {
      if (levelFilter === 'ERROR' && log.level !== 'ERROR' && log.level !== 'CRITICAL') return false;
      if (
        levelFilter === 'WARNING' &&
        log.level !== 'WARNING' && log.level !== 'ERROR' && log.level !== 'CRITICAL'
      ) return false;
      if (!needle) return true;
      return (
        log.message.toLowerCase().includes(needle) ||
        log.source.toLowerCase().includes(needle)
      );
    });
  }, [logs, levelFilter, query]);

  const exportLogsToCSV = () => {
    if (!logs.length) return;

    const escape = (value: string) => `"${String(value).replace(/"/g, '""')}"`;
    const csvContent = [
      ['Timestamp', 'Level', 'Source', 'Message'].join(','),
      ...logs.map(log => [
        new Date(log.timestamp).toISOString(),
        log.level,
        escape(log.source),
        escape(log.message),
      ].join(',')),
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `scanner_logs_${new Date().toISOString().split('T')[0]}.csv`;
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Логи</h1>
          <p className="text-sm text-slate-400">
            Останні {logs.length} записів сканера · оновлюється кожні 5 секунд
          </p>
        </div>

        <button
          onClick={exportLogsToCSV}
          disabled={!logs.length}
          className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 hover:text-white text-xs font-bold rounded-xl transition-colors focus:ring-2 focus:ring-slate-500/50 outline-none shrink-0"
          title="Вивантажити весь буфер у CSV"
        >
          <Download className="w-4 h-4" />
          Експорт CSV
        </button>
      </div>

      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-slate-900/50 border border-slate-800/50 rounded-3xl overflow-hidden"
      >
        <div className="p-4 md:p-6 border-b border-slate-800/50 flex flex-col sm:flex-row gap-3 sm:items-center">
          <div className="relative flex-1 min-w-0">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Пошук за текстом або джерелом…"
              className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all"
            />
          </div>

          <div className="flex items-center gap-1 bg-slate-950 border border-slate-800 rounded-lg p-1 shrink-0">
            {([
              ['ALL', 'Усі'],
              ['WARNING', 'Попередження'],
              ['ERROR', 'Помилки'],
            ] as const).map(([level, label]) => (
              <button
                key={level}
                onClick={() => setLevelFilter(level)}
                className={cn(
                  'px-3 py-1.5 rounded-md text-[11px] font-bold uppercase tracking-wider transition-colors',
                  levelFilter === level ? 'bg-slate-800 text-white' : 'text-slate-500 hover:text-slate-300'
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="p-4 md:p-6">
          <div className="flex items-center gap-2 mb-4 text-sm font-bold uppercase tracking-wider text-slate-400">
            <Clock className="w-4 h-4" />
            Лог сканера
            {visibleLogs.length !== logs.length && (
              <span className="text-[11px] font-medium normal-case tracking-normal text-slate-500">
                показано {visibleLogs.length} з {logs.length}
              </span>
            )}
          </div>

          <div className="space-y-2 max-h-[36rem] overflow-y-auto">
            {visibleLogs.length > 0 ? visibleLogs.map((log, index) => (
              <div
                key={`${log.timestamp}-${index}`}
                className="bg-slate-950/50 border border-slate-800/50 rounded-xl px-4 py-3 flex items-start gap-3"
              >
                <div className={cn('mt-0.5 shrink-0', LEVEL_STYLES[log.level]?.color ?? 'text-slate-400')}>
                  {LEVEL_STYLES[log.level]?.icon ?? <Activity className="w-4 h-4" />}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="text-sm text-slate-200 break-words">{log.message}</div>
                  <div className="text-[11px] text-slate-500 font-mono tabular-nums mt-1">
                    {new Date(log.timestamp).toLocaleTimeString('uk-UA')} • {log.source}
                  </div>
                </div>

                <span
                  className={cn(
                    'shrink-0 px-2 py-0.5 rounded-md text-[10px] font-black tracking-wider',
                    LEVEL_STYLES[log.level]?.badge ?? 'bg-slate-800 text-slate-400'
                  )}
                >
                  {log.level}
                </span>
              </div>
            )) : (
              <div className="text-center py-10 text-slate-400 text-sm">
                {isLoading
                  ? 'Читаю лог…'
                  : logs.length === 0
                    ? 'Лог порожній — сканер ще нічого не написав.'
                    : 'Нічого не знайдено за цим фільтром.'}
              </div>
            )}
          </div>
        </div>
      </motion.section>
    </div>
  );
}
