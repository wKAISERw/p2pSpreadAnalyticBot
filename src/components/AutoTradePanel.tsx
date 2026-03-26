import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Play, Square, Settings2, AlertTriangle, CheckCircle2, XCircle, Clock, Activity, Download } from 'lucide-react';
import { AutoTradeConfig, AutoTradeLog } from '../types';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';
import useSWR from 'swr';
import { api } from '../services/api';

export default function AutoTradePanel() {
  const { userSettings, setUserSettings } = useAppStore();
  const config = userSettings.autoTrade!;
  
  const { data: logs = [] } = useSWR('/logs', api.getLogs, { refreshInterval: 5000 });

  const [isEditing, setIsEditing] = useState(false);
  const [localConfig, setLocalConfig] = useState<AutoTradeConfig>(config);

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
    
    const headers = ['Timestamp', 'Status', 'Message', 'Expected Profit', 'Amount UAH', 'Exchange'];
    const csvContent = [
      headers.join(','),
      ...logs.map(log => [
        new Date(log.timestamp).toISOString(),
        log.status,
        `"${log.errorMessage || ''}"`,
        log.expectedProfit || '',
        log.amountUah || '',
        `${log.buyExchange} -> ${log.sellExchange}`
      ].join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `autotrade_logs_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-[2rem] overflow-hidden">
      <div className="p-4 md:p-6 border-b border-slate-800 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 md:gap-0 bg-slate-900/50">
        <div className="flex items-center gap-4">
          <div className={cn(
            "w-10 h-10 md:w-12 md:h-12 rounded-2xl flex items-center justify-center shadow-lg transition-colors shrink-0",
            config.enabled ? "bg-emerald-500 shadow-emerald-500/20" : "bg-slate-800 shadow-slate-900/20"
          )}>
            <Activity className={cn("w-5 h-5 md:w-6 md:h-6", config.enabled ? "text-slate-950" : "text-slate-400")} />
          </div>
          <div>
            <h2 className="text-lg md:text-xl font-bold text-white">Auto-Trading Engine</h2>
            <p className="text-[10px] md:text-xs text-slate-400 uppercase tracking-widest font-semibold">
              {config.enabled ? 'Active & Scanning' : 'Currently Paused'}
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
              "flex-1 md:flex-none flex items-center justify-center gap-2 px-6 py-2.5 text-sm font-bold rounded-xl transition-all shadow-lg focus:ring-2 focus:ring-emerald-500/50 outline-none",
              config.enabled 
                ? "bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/20" 
                : "bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-emerald-500/20"
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
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all tabular-nums"
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
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all tabular-nums"
                  />
                </div>
              </div>
              
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">
                    Allowed Exchanges
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {['Binance', 'Bybit', 'OKX', 'MEXC'].map(ex => (
                      <motion.button
                        whileHover={{ scale: 1.05 }}
                        whileTap={{ scale: 0.95 }}
                        key={ex}
                        onClick={() => toggleExchange(ex)}
                        className={cn(
                          "px-4 py-2 rounded-xl text-xs font-bold transition-all border focus:ring-2 focus:ring-emerald-500/50 outline-none",
                          localConfig.allowedExchanges.includes(ex)
                            ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
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
                    className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                  />
                  <div className="flex justify-between mt-2 font-mono text-xs text-slate-400 tabular-nums">
                    <span>0 (Safest)</span>
                    <span className="text-emerald-500 font-bold">{localConfig.maxRiskScore}</span>
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
                className="px-6 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold rounded-xl transition-all shadow-lg shadow-emerald-500/20 focus:ring-2 focus:ring-emerald-500/50 outline-none"
              >
                SAVE CONFIG
              </motion.button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="p-6">
        <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 mb-4 flex items-center gap-2">
          <Clock className="w-4 h-4" />
          Execution Log
        </h3>
        
        <div className="space-y-3">
          {logs.length > 0 ? logs.map(log => (
            <div key={log.id} className="bg-slate-950/50 border border-slate-800/50 rounded-2xl p-4 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className={cn(
                  "w-10 h-10 rounded-xl flex items-center justify-center",
                  log.status === 'SUCCESS' ? "bg-emerald-500/10 text-emerald-500" :
                  log.status === 'FAILED' ? "bg-red-500/10 text-red-500" :
                  "bg-blue-500/10 text-blue-500"
                )}>
                  {log.status === 'SUCCESS' ? <CheckCircle2 className="w-5 h-5" /> :
                   log.status === 'FAILED' ? <XCircle className="w-5 h-5" /> :
                   <Clock className="w-5 h-5 animate-pulse" />}
                </div>
                <div>
                  <div className="text-sm font-bold text-white">
                    {log.buyExchange} → {log.sellExchange}
                  </div>
                  <div className="text-xs text-slate-400 font-mono tabular-nums">
                    {new Date(log.timestamp).toLocaleTimeString()} • {log.amountUah} ₴
                  </div>
                </div>
              </div>
              
              <div className="text-right">
                <div className={cn(
                  "text-sm font-black tabular-nums",
                  log.status === 'SUCCESS' ? "text-emerald-400" : "text-slate-400"
                )}>
                  +{log.expectedProfit.toFixed(0)} ₴
                </div>
                {log.errorMessage && (
                  <div className="text-xs text-red-400 max-w-[150px] truncate" title={log.errorMessage}>
                    {log.errorMessage}
                  </div>
                )}
              </div>
            </div>
          )) : (
            <div className="text-center py-8 text-slate-400 text-sm">
              No auto-trades executed yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
