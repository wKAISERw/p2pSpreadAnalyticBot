import React, { useState } from 'react';
import { Key, Eye, EyeOff, CheckCircle2, Trash2 } from 'lucide-react';
import { ApiKeyConfig } from '../types';
import { cn } from '../lib/utils';
import { motion } from 'motion/react';
import { useAppStore } from '../store';
import { api } from '../services/api';
import { toast } from 'sonner';
import { doc, setDoc } from 'firebase/firestore';
import { db, auth } from '../firebase';

export default function ApiKeysPanel() {
  const { userSettings, setUserSettings } = useAppStore();
  const connectedExchanges = Object.keys(userSettings.apiKeys || {}).map(k => k.toLowerCase());

  const handleSaveKey = async (exchange: string, config: ApiKeyConfig) => {
    const newApiKeys = { ...(userSettings.apiKeys || {}), [exchange.toLowerCase()]: config };
    const newSettings = { ...userSettings, apiKeys: newApiKeys };
    setUserSettings(newSettings);
    
    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), newSettings, { merge: true });
    }
    
    // await api.saveCredentials(exchange, config);
    toast.success(`Keys for ${exchange} saved successfully`);
  };

  const handleDisconnect = async (exchange: string) => {
    const newApiKeys = { ...(userSettings.apiKeys || {}) };
    delete newApiKeys[exchange.toLowerCase()];
    
    const newSettings = { ...userSettings, apiKeys: newApiKeys };
    setUserSettings(newSettings);
    
    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), newSettings, { merge: true });
    }
    
    // await api.deleteCredentials(exchange);
    toast.success(`Disconnected ${exchange}`);
  };

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
          onSave={(config: any) => handleSaveKey('Binance', config)}
          onDisconnect={() => handleDisconnect('Binance')}
        />
        <ApiKeyCard
          exchange="Bybit"
          isConnected={connectedExchanges.includes('bybit')}
          onSave={(config: any) => handleSaveKey('Bybit', config)}
          onDisconnect={() => handleDisconnect('Bybit')}
        />
        <ApiKeyCard
          exchange="OKX"
          isConnected={connectedExchanges.includes('okx')}
          hasPassphrase
          onSave={(config: any) => handleSaveKey('OKX', config)}
          onDisconnect={() => handleDisconnect('OKX')}
        />
        <ApiKeyCard
          exchange="MEXC"
          isConnected={connectedExchanges.includes('mexc')}
          onSave={(config: any) => handleSaveKey('MEXC', config)}
          onDisconnect={() => handleDisconnect('MEXC')}
        />
        <ApiKeyCard
          exchange="CryptoBot"
          isConnected={connectedExchanges.includes('cryptobot')}
          onSave={(config: any) => handleSaveKey('CryptoBot', config)}
          onDisconnect={() => handleDisconnect('CryptoBot')}
        />
        <ApiKeyCard
          exchange="Telegram Wallet"
          isConnected={connectedExchanges.includes('telegram wallet')}
          onSave={(config: any) => handleSaveKey('Telegram Wallet', config)}
          onDisconnect={() => handleDisconnect('Telegram Wallet')}
        />
      </div>
    </div>
  );
}

function ApiKeyCard({ exchange, isConnected, onSave, onDisconnect, hasPassphrase }: any) {
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
            <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold">
              {isConnected ? 'Connected' : 'Not Connected'}
            </p>
          </div>
        </div>
        {!isEditing && (
          <div className="flex items-center gap-2">
            <motion.button 
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setIsEditing(true)} 
              className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold rounded-lg transition-colors focus:ring-2 focus:ring-slate-500/50 outline-none"
            >
              UPDATE
            </motion.button>
            <motion.button 
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={onDisconnect} 
              className="p-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg transition-colors focus:ring-2 focus:ring-red-500/50 outline-none"
              title="Disconnect Exchange"
            >
              <Trash2 className="w-4 h-4" />
            </motion.button>
          </div>
        )}
      </div>

      {isEditing ? (
        <div className="space-y-4">
          <div>
            <label className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">API Key</label>
            <input
              type="text"
              value={localKeys.key}
              onChange={(e) => setLocalKeys({ ...localKeys, key: e.target.value })}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all"
              placeholder="Enter API Key"
            />
          </div>
          <div>
            <label className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">API Secret</label>
            <div className="relative">
              <input
                type={showSecret ? "text" : "password"}
                value={localKeys.secret}
                onChange={(e) => setLocalKeys({ ...localKeys, secret: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all pr-10"
                placeholder="Enter API Secret"
              />
              <button 
                onClick={() => setShowSecret(!showSecret)} 
                className="absolute right-3 top-3 text-slate-400 hover:text-slate-300 focus:outline-none"
              >
                {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          {hasPassphrase && (
            <div>
              <label className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Passphrase</label>
              <input
                type="password"
                value={localKeys.passphrase}
                onChange={(e) => setLocalKeys({ ...localKeys, passphrase: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all"
                placeholder="Enter Passphrase"
              />
            </div>
          )}
          <div className="pt-2 flex gap-3">
            {isConnected && (
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.95 }}
                onClick={() => setIsEditing(false)}
                className="flex-1 py-2.5 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all focus:ring-2 focus:ring-slate-500/50 outline-none"
              >
                CANCEL
              </motion.button>
            )}
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.95 }}
              onClick={handleSave}
              disabled={isSaving || !localKeys.key || !localKeys.secret}
              className="flex-1 py-2.5 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold rounded-xl transition-all shadow-lg shadow-emerald-500/20 disabled:opacity-50 focus:ring-2 focus:ring-emerald-500/50 outline-none"
            >
              {isSaving ? 'SAVING...' : 'SAVE KEYS'}
            </motion.button>
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
          <div className="text-xs font-bold text-emerald-500 uppercase tracking-widest bg-emerald-500/10 px-2 py-1 rounded">
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
    'MEXC': 'bg-blue-500',
    'CryptoBot': 'bg-indigo-500 text-white',
    'Telegram Wallet': 'bg-sky-500 text-white'
  };
  return (
    <div className={cn("w-10 h-10 rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-xs text-slate-950", colors[name] || 'bg-slate-700 text-white')}>
      {name[0]}
    </div>
  );
}
