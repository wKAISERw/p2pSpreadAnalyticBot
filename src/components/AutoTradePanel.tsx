import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Play, Square, Settings2, AlertTriangle, CheckCircle2, XCircle, Clock, Activity, Download } from 'lucide-react';
import { AutoTradeConfig, LogRecord } from '../types';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';
import useSWR from 'swr';
import { api } from '../services/api';
import { useExchanges } from '../hooks/useExchanges';

const LEVEL_STYLES: Record<string, { color: string; badge: string; icon: React.ReactNode }> = {
  CRITICAL: { color: 'text-red-500', badge: 'bg-red-500/20 text-red-400', icon: <XCircle className="w-4 h-4" /> },
  ERROR: { color: 'text-red-500', badge: 'bg-red-500/10 text-red-400', icon: <XCircle className="w-4 h-4" /> },
  WARNING: { color: 'text-orange-400', badge: 'bg-orange-500/10 text-orange-400', icon: <AlertTriangle className="w-4 h-4" /> },
  INFO: { color: 'text-blue-400', badge: 'bg-blue-500/10 text-blue-400', icon: <CheckCircle2 className="w-4 h-4" /> },
  DEBUG: { color: 'text-slate-500', badge: 'bg-slate-800 text-slate-400', icon: <Activity className="w-4 h-4" /> },
};

export default function AutoTradePanel() {
  const { userSettings, setUserSettings } = useAppStore();
  const config = userSettings.autoTrade!;

  // /logs віддає записи логу сканера, а не журнал угод. Поки ендпоінт
  // завжди повертав [], панель цього не помічала і типізувала їх як
  // AutoTradeLog — з першим же реальним записом вона падала на
  // log.expectedProfit.toFixed().
  const { data: logs = [] } = useSWR<LogRecord[]>('/logs', () => api.getLogs(200), {
    refreshInterval: 5000,
    shouldRetryOnError: false,
  });

  const [isEditing, setIsEditing] = useState(false);
  const [localConfig, setLocalConfig] = useState<AutoTradeConfig>(config);
  const [levelFilter, setLevelFilter] = useState<'ALL' | 'WARNING' | 'ERROR'>('ALL');
  const { names: exchangeNames } = useExchanges();

  const visibleLogs = logs.filter(log => {
    if (levelFilter === 'ALL') return true;
    if (levelFilter === 'ERROR') return log.level === 'ERROR' || log.level === 'CRITICAL';
    return log.level === 'WARNING' || log.level === 'ERROR' || log.level === 'CRITICAL';
  });

  useEffect(() => {
    setLocalConfig(config);
  }, [config]);

  const handleSave = () => {
    setUserSettings({ ...userSettings, autoTrade: localConfig });
    setIsEditing(false);
  };

  const toggleExchange = (exchange: string) => {
    const newExchanges = localConfig.allowedExchanges.includes(exchange)
      ? localConfig.allowedExchanges.filter(e => e !== exchange)
      : [...localConfig.allowedExchanges, exchange];
    setLocalConfig({ ...localConfig, allowedExchanges: newExchanges });
  };

  const exportLogsToCSV = () => {
    if (!logs.length) return;

    const escape = (value: string) => `"${String(value).replace(/"/g, '""')}"`;
    const headers = ['Timestamp', 'Level', 'Source', 'Message'];
    const csvContent = [
      headers.join(','),
      ...logs.map(log => [
        new Date(log.timestamp).toISOString(),
        log.level,
        escape(log.source),
        escape(log.message),
      ].join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `scanner_logs_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-[2rem] overflow-hidden">
      <div className="p-4 md:p-6 border-b border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 md:gap-0 bg-slate-900/50">
        <div className="flex items-center gap-4">
          <div className={cn(
            "w-10 h-10 md:w-12 md:h-12 rounded-2xl flex items-center justify-center shadow-lg transition-colors shrink-0",
            config.enabled ? "bg-accent-500 shadow-accent-500/20" : "bg-slate-800 shadow-slate-900/20"
          )}>
            <Activity className={cn("w-5 h-5 md:w-6 md:h-6", config.enabled ? "text-slate-950" : "text-slate-400")} />
          </div>
          <div>
            <h2 className="text-lg md:text-xl font-bold text-white">Auto-Trading Engine</h2>
            <p className="text-[10px] md:text-xs text-slate-400 uppercase tracking-widest font-semibold">
              {config.enabled ? 'Active & Scanning' : 'Currently Paused'}
            </p>
            {/* Виконанням угод керує trade_worker у боті, HTTP-ендпоінта для
                нього немає. Щоб цей пресет не виглядав як «запустив торгівлю». */}
            <p className="text-[10px] text-slate-500 mt-0.5">
              Локальний пресет — виконання угод налаштовується в Telegram-боті
            </p>
          </div>
        </div>
        
        <div className="flex items-center gap-3 w-full md:w-auto justify-between md:justify-end">
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={exportLogsToCSV}
            className="p-2.5 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white rounded-xl transition-colors focus:ring-2 focus:ring-slate-500/50 outline-none"
            title="Export Logs to CSV"
          >
            <Download className="w-5 h-5" />
          </motion.button>
          <motion.button
            whileHover={{ rotate: 90, scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
            transition={{ type: "spring", stiffness: 300, damping: 20 }}
            onClick={() => setIsEditing(!isEditing)}
            className="p-2.5 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white rounded-xl transition-colors focus:ring-2 focus:ring-slate-500/50 outline-none"
          >
            <Settings2 className="w-5 h-5" />
          </motion.button>
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => setUserSettings({ ...userSettings, autoTrade: { ...config, enabled: !config.enabled } })}
            className={cn(
              "flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2.5 text-sm font-bold rounded-xl transition-all shadow-lg focus:ring-2 focus:ring-accent-500/50 outline-none",
              config.enabled 
                ? "bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/20" 
                : "bg-accent-500 hover:bg-accent-400 text-slate-950 shadow-accent-500/20"
            )}
          >
            {config.enabled ? (
              <><Square className="w-4 h-4 fill-current" /> STOP</>
            ) : (
              <><Play className="w-4 h-4 fill-current" /> START</>
            )}
          </motion.button>
        </div>
      </div>

      <AnimatePresence>
        {isEditing && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="border-b border-slate-800 bg-slate-950/50"
          >
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">
                    Max Trade Amount (UAH)
                  </label>
                  <input
                    type="number"
                    value={localConfig.maxTradeAmount}
                    onChange={(e) => setLocalConfig({ ...localConfig, maxTradeAmount: Number(e.target.value) })}
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all tabular-nums"
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">
                    Min Spread (%)
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    value={localConfig.minSpread}
                    onChange={(e) => setLocalConfig({ ...localConfig, minSpread: Number(e.target.value) })}
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all tabular-nums"
                  />
                </div>
              </div>
              
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">
                    Allowed Exchanges
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {exchangeNames.map(ex => (
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        key={ex}
                        onClick={() => toggleExchange(ex)}
                        className={cn(
                          "px-4 py-2 rounded-xl text-xs font-bold transition-all border focus:ring-2 focus:ring-accent-500/50 outline-none",
                          localConfig.allowedExchanges.includes(ex)
                            ? "bg-accent-500/10 border-accent-500/30 text-accent-400"
                            : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700"
                        )}
                      >
                        {ex}
                      </motion.button>
                    ))}
                  </div>
                </div>
                
                <div>
                  <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">
                    Max Risk Score
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="10"
                    value={localConfig.maxRiskScore}
                    onChange={(e) => setLocalConfig({ ...localConfig, maxRiskScore: Number(e.target.value) })}
                    className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-accent-500"
                  />
                  <div className="flex justify-between mt-2 font-mono text-xs text-slate-400 tabular-nums">
                    <span>0 (Safest)</span>
                    <span className="text-accent-500 font-bold">{localConfig.maxRiskScore}</span>
                    <span>100 (Risky)</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="px-6 py-4 bg-slate-900/30 flex justify-end gap-3 border-t border-slate-800">
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.95 }}
                onClick={() => setIsEditing(false)}
                className="px-6 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all focus:ring-2 focus:ring-slate-500/50 outline-none"
              >
                CANCEL
              </motion.button>
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.95 }}
                onClick={handleSave}
                className="px-6 py-2 bg-accent-500 hover:bg-accent-400 text-slate-950 text-xs font-bold rounded-xl transition-all shadow-lg shadow-accent-500/20 focus:ring-2 focus:ring-accent-500/50 outline-none"
              >
                SAVE CONFIG
              </motion.button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
            <Clock className="w-4 h-4" />
            Лог сканера
          </h3>
          <div className="flex items-center gap-1 bg-slate-950 border border-slate-800 rounded-lg p-1">
            {(['ALL', 'WARNING', 'ERROR'] as const).map(level => (
              <button
                key={level}
                onClick={() => setLevelFilter(level)}
                className={cn(
                  "px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider transition-colors",
                  levelFilter === level ? "bg-slate-800 text-white" : "text-slate-500 hover:text-slate-300"
                )}
              >
                {level}
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-2 max-h-[32rem] overflow-y-auto">
          {visibleLogs.length > 0 ? visibleLogs.map((log, index) => (
            <div
              key={`${log.timestamp}-${index}`}
              className="bg-slate-950/50 border border-slate-800/50 rounded-xl px-4 py-3 flex items-start gap-3"
            >
              <div className={cn("mt-0.5 shrink-0", LEVEL_STYLES[log.level]?.color ?? "text-slate-400")}>
                {LEVEL_STYLES[log.level]?.icon ?? <Activity className="w-4 h-4" />}
              </div>

              <div className="min-w-0 flex-1">
                <div className="text-sm text-slate-200 break-words">{log.message}</div>
                <div className="text-[11px] text-slate-500 font-mono tabular-nums mt-1">
                  {new Date(log.timestamp).toLocaleTimeString()} • {log.source}
                </div>
              </div>

              <span
                className={cn(
                  "shrink-0 px-2 py-0.5 rounded-md text-[10px] font-black tracking-wider",
                  LEVEL_STYLES[log.level]?.badge ?? "bg-slate-800 text-slate-400"
                )}
              >
                {log.level}
              </span>
            </div>
          )) : (
            <div className="text-center py-8 text-slate-400 text-sm">
              {logs.length === 0 ? 'Лог порожній — сканер ще нічого не написав.' : 'Немає записів цього рівня.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
