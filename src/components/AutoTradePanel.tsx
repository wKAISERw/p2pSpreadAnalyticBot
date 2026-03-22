import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Play, Square, Settings2, AlertTriangle, CheckCircle2, XCircle, Clock, Activity } from 'lucide-react';
import { AutoTradeConfig, AutoTradeLog } from '../types';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface AutoTradePanelProps {
  config: AutoTradeConfig;
  onConfigChange: (newConfig: AutoTradeConfig) => void;
  logs: AutoTradeLog[];
}

export default function AutoTradePanel({ config, onConfigChange, logs }: AutoTradePanelProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [localConfig, setLocalConfig] = useState<AutoTradeConfig>(config);

  useEffect(() => {
    setLocalConfig(config);
  }, [config]);

  const handleSave = () => {
    onConfigChange(localConfig);
    setIsEditing(false);
  };

  const toggleExchange = (exchange: string) => {
    const newExchanges = localConfig.allowedExchanges.includes(exchange)
      ? localConfig.allowedExchanges.filter(e => e !== exchange)
      : [...localConfig.allowedExchanges, exchange];
    setLocalConfig({ ...localConfig, allowedExchanges: newExchanges });
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-[2rem] overflow-hidden">
      <div className="p-6 border-b border-slate-800 flex items-center justify-between bg-slate-900/50">
        <div className="flex items-center gap-4">
          <div className={cn(
            "w-12 h-12 rounded-2xl flex items-center justify-center shadow-lg transition-colors",
            config.enabled ? "bg-emerald-500 shadow-emerald-500/20" : "bg-slate-800 shadow-slate-900/20"
          )}>
            <Activity className={cn("w-6 h-6", config.enabled ? "text-slate-950" : "text-slate-400")} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">Auto-Trading Engine</h2>
            <p className="text-xs text-slate-500 uppercase tracking-widest font-semibold">
              {config.enabled ? 'Active & Scanning' : 'Currently Paused'}
            </p>
          </div>
        </div>
        
        <div className="flex items-center gap-3">
          <button
            onClick={() => setIsEditing(!isEditing)}
            className="p-2.5 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white rounded-xl transition-colors"
          >
            <Settings2 className="w-5 h-5" />
          </button>
          <button
            onClick={() => onConfigChange({ ...config, enabled: !config.enabled })}
            className={cn(
              "flex items-center gap-2 px-6 py-2.5 text-sm font-bold rounded-xl transition-all shadow-lg",
              config.enabled 
                ? "bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/20" 
                : "bg-emerald-500 hover:bg-emerald-400 text-slate-950 shadow-emerald-500/20"
            )}
          >
            {config.enabled ? (
              <><Square className="w-4 h-4 fill-current" /> STOP ENGINE</>
            ) : (
              <><Play className="w-4 h-4 fill-current" /> START ENGINE</>
            )}
          </button>
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
                  <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">
                    Max Trade Amount (UAH)
                  </label>
                  <input
                    type="number"
                    value={localConfig.maxTradeAmount}
                    onChange={(e) => setLocalConfig({ ...localConfig, maxTradeAmount: Number(e.target.value) })}
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 outline-none transition-all"
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">
                    Min Spread (%)
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    value={localConfig.minSpread}
                    onChange={(e) => setLocalConfig({ ...localConfig, minSpread: Number(e.target.value) })}
                    className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 outline-none transition-all"
                  />
                </div>
              </div>
              
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">
                    Allowed Exchanges
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {['Binance', 'Bybit', 'OKX', 'MEXC'].map(ex => (
                      <button
                        key={ex}
                        onClick={() => toggleExchange(ex)}
                        className={cn(
                          "px-4 py-2 rounded-xl text-xs font-bold transition-all border",
                          localConfig.allowedExchanges.includes(ex)
                            ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-400"
                            : "bg-slate-900 border-slate-800 text-slate-500 hover:border-slate-700"
                        )}
                      >
                        {ex}
                      </button>
                    ))}
                  </div>
                </div>
                
                <div>
                  <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">
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
                  <div className="flex justify-between mt-2 font-mono text-xs text-slate-400">
                    <span>0 (Safest)</span>
                    <span className="text-emerald-500 font-bold">{localConfig.maxRiskScore}</span>
                    <span>100 (Risky)</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="px-6 py-4 bg-slate-900/30 flex justify-end gap-3 border-t border-slate-800">
              <button
                onClick={() => setIsEditing(false)}
                className="px-6 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all"
              >
                CANCEL
              </button>
              <button
                onClick={handleSave}
                className="px-6 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold rounded-xl transition-all shadow-lg shadow-emerald-500/20"
              >
                SAVE CONFIG
              </button>
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
                  <div className="text-xs text-slate-500 font-mono">
                    {new Date(log.timestamp).toLocaleTimeString()} • {log.amountUah} ₴
                  </div>
                </div>
              </div>
              
              <div className="text-right">
                <div className={cn(
                  "text-sm font-black",
                  log.status === 'SUCCESS' ? "text-emerald-400" : "text-slate-500"
                )}>
                  +{log.expectedProfit.toFixed(0)} ₴
                </div>
                {log.errorMessage && (
                  <div className="text-[10px] text-red-400 max-w-[150px] truncate" title={log.errorMessage}>
                    {log.errorMessage}
                  </div>
                )}
              </div>
            </div>
          )) : (
            <div className="text-center py-8 text-slate-500 text-sm">
              No auto-trades executed yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
