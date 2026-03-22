import React, { useState } from 'react';
import { Key, Eye, EyeOff, CheckCircle2, AlertTriangle } from 'lucide-react';
import { ApiKeyConfig } from '../types';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface ApiKeysPanelProps {
  connectedExchanges: string[];
  onSaveKey: (exchange: string, config: ApiKeyConfig) => Promise<void>;
}

export default function ApiKeysPanel({ connectedExchanges, onSaveKey }: ApiKeysPanelProps) {
  return (
    <div className="space-y-6">
      <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-3xl p-6 mb-8">
        <div className="flex items-start gap-4">
          <div className="p-2 bg-emerald-500/20 rounded-xl">
            <Key className="w-6 h-6 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-emerald-400 mb-1">API Credentials Management</h2>
            <p className="text-sm text-emerald-500/80 leading-relaxed">
              Connect your exchange API keys to enable real-time balance tracking, deep merchant profiling, and automated trading. 
              Keys are encrypted using AES-128-CBC before being stored in the database.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <ApiKeyCard
          exchange="Binance"
          isConnected={connectedExchanges.includes('binance')}
          onSave={(config) => onSaveKey('Binance', config)}
        />
        <ApiKeyCard
          exchange="Bybit"
          isConnected={connectedExchanges.includes('bybit')}
          onSave={(config) => onSaveKey('Bybit', config)}
        />
        <ApiKeyCard
          exchange="OKX"
          isConnected={connectedExchanges.includes('okx')}
          hasPassphrase
          onSave={(config) => onSaveKey('OKX', config)}
        />
        <ApiKeyCard
          exchange="MEXC"
          isConnected={connectedExchanges.includes('mexc')}
          onSave={(config) => onSaveKey('MEXC', config)}
        />
      </div>
    </div>
  );
}

function ApiKeyCard({ exchange, isConnected, onSave, hasPassphrase }: any) {
  const [isEditing, setIsEditing] = useState(!isConnected);
  const [showSecret, setShowSecret] = useState(false);
  const [localKeys, setLocalKeys] = useState<ApiKeyConfig>({ key: '', secret: '', passphrase: '' });
  const [isSaving, setIsSaving] = useState(false);

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSave(localKeys);
      setIsEditing(false);
    } catch (err) {
      console.error("Failed to save credentials", err);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className={cn(
      "bg-slate-900 border rounded-3xl p-6 transition-all",
      isConnected && !isEditing ? "border-emerald-500/30" : "border-slate-800"
    )}>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ExchangeIcon name={exchange} />
          <div>
            <h3 className="font-bold text-white text-lg">{exchange}</h3>
            <p className="text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
              {isConnected ? 'Connected' : 'Not Connected'}
            </p>
          </div>
        </div>
        {!isEditing && (
          <button 
            onClick={() => setIsEditing(true)} 
            className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold rounded-lg transition-colors"
          >
            UPDATE
          </button>
        )}
      </div>

      {isEditing ? (
        <div className="space-y-4">
          <div>
            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">API Key</label>
            <input
              type="text"
              value={localKeys.key}
              onChange={(e) => setLocalKeys({ ...localKeys, key: e.target.value })}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 outline-none transition-all"
              placeholder="Enter API Key"
            />
          </div>
          <div>
            <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">API Secret</label>
            <div className="relative">
              <input
                type={showSecret ? "text" : "password"}
                value={localKeys.secret}
                onChange={(e) => setLocalKeys({ ...localKeys, secret: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 outline-none transition-all pr-10"
                placeholder="Enter API Secret"
              />
              <button 
                onClick={() => setShowSecret(!showSecret)} 
                className="absolute right-3 top-3 text-slate-500 hover:text-slate-300"
              >
                {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          {hasPassphrase && (
            <div>
              <label className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1.5 block">Passphrase</label>
              <input
                type="password"
                value={localKeys.passphrase}
                onChange={(e) => setLocalKeys({ ...localKeys, passphrase: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 outline-none transition-all"
                placeholder="Enter Passphrase"
              />
            </div>
          )}
          <div className="pt-2 flex gap-3">
            {isConnected && (
              <button
                onClick={() => setIsEditing(false)}
                className="flex-1 py-2.5 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all"
              >
                CANCEL
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={isSaving || !localKeys.key || !localKeys.secret}
              className="flex-1 py-2.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold rounded-xl transition-all shadow-lg shadow-emerald-500/20 disabled:opacity-50"
            >
              {isSaving ? 'SAVING...' : 'SAVE KEYS'}
            </button>
          </div>
        </div>
      ) : (
        <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <CheckCircle2 className="w-5 h-5 text-emerald-500" />
            <div className="text-sm font-mono text-slate-400">
              ••••••••••••••••••••
            </div>
          </div>
          <div className="text-[10px] font-bold text-emerald-500 uppercase tracking-widest bg-emerald-500/10 px-2 py-1 rounded">
            Active
          </div>
        </div>
      )}
    </div>
  );
}

function ExchangeIcon({ name }: { name: string }) {
  const colors: Record<string, string> = {
    'Bybit': 'bg-orange-500',
    'OKX': 'bg-white',
    'Binance': 'bg-yellow-400',
    'MEXC': 'bg-blue-500'
  };
  return (
    <div className={cn("w-10 h-10 rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-xs text-slate-950", colors[name] || 'bg-slate-700')}>
      {name[0]}
    </div>
  );
}
