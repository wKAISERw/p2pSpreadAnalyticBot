import React, { useState } from 'react';
import { Settings, ShieldAlert, CheckCircle2, Filter, Shield, Bot, Zap, Pin, MessageSquare, Bell, Send, Download, Upload, RefreshCw } from 'lucide-react';
import { GlobalSettings, UserSettings } from '../types';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface SettingsPanelProps {
  globalSettings: GlobalSettings;
  userSettings: UserSettings;
  onGlobalChange: (key: keyof GlobalSettings, value: any) => void;
  onUserChange: (key: keyof UserSettings, value: any) => void;
  isAdmin: boolean;
  onConnectTelegram: (telegramId: string) => Promise<void>;
  onDisconnectTelegram: () => Promise<void>;
  onSyncFromBot: () => Promise<void>;
  onSyncToBot: () => Promise<void>;
}

export default function SettingsPanel({ globalSettings, userSettings, onGlobalChange, onUserChange, isAdmin, onConnectTelegram, onDisconnectTelegram, onSyncFromBot, onSyncToBot }: SettingsPanelProps) {
  const [tgInput, setTgInput] = useState('');
  const [isConnecting, setIsConnecting] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  const handleConnect = async () => {
    if (!tgInput) return;
    setIsConnecting(true);
    await onConnectTelegram(tgInput);
    setIsConnecting(false);
  };

  const handleSyncFromBot = async () => {
    setIsSyncing(true);
    await onSyncFromBot();
    setIsSyncing(false);
  };

  const handleSyncToBot = async () => {
    setIsSyncing(true);
    await onSyncToBot();
    setIsSyncing(false);
  };

  const toggleAutoSync = () => {
    onUserChange('autoSyncTelegram', !userSettings.autoSyncTelegram);
  };

  const toggleBank = (code: string) => {
    const newBanks = userSettings.banks.includes(code)
      ? userSettings.banks.filter(b => b !== code)
      : [...userSettings.banks, code];
    onUserChange('banks', newBanks);
  };

  const handleMerchantFilterChange = (key: 'minOrders' | 'minRate', value: number) => {
    const currentFilters = userSettings.merchantFilters || { minOrders: 0, minRate: 0 };
    onUserChange('merchantFilters', { ...currentFilters, [key]: value });
  };

  const averageCapital = Math.floor((userSettings.minCapital + userSettings.maxCapital) / 2);

  return (
    <div className="space-y-8">
      {/* Telegram Sync Section */}
      <section className="bg-blue-500/10 border border-blue-500/20 rounded-3xl p-6">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-4">
            <div className="p-3 bg-blue-500/20 rounded-xl">
              <Send className="w-6 h-6 text-blue-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-blue-400">Telegram Bot Sync</h2>
              <p className="text-sm text-blue-500/80">
                {userSettings.telegramUserId
                  ? `Connected to Telegram ID: ${userSettings.telegramUserId}`
                  : "Connect your Telegram account to sync settings, API keys, and admin rights."}
              </p>
            </div>
          </div>
          
          {!userSettings.telegramUserId ? (
            <div className="flex w-full md:w-auto gap-2">
              <input 
                type="text" 
                placeholder="Enter Telegram ID..." 
                value={tgInput}
                onChange={(e) => setTgInput(e.target.value)}
                className="bg-slate-950 border border-blue-500/30 rounded-xl px-4 py-2 text-sm text-white focus:border-blue-500 outline-none w-full md:w-48"
              />
              <button 
                onClick={handleConnect}
                disabled={isConnecting || !tgInput}
                className="px-4 py-2 bg-blue-500 hover:bg-blue-400 text-slate-950 font-bold rounded-xl transition-all disabled:opacity-50 whitespace-nowrap"
              >
                {isConnecting ? 'Connecting...' : 'Connect'}
              </button>
            </div>
          ) : (
            <button 
              onClick={onDisconnectTelegram}
              className="px-4 py-2 bg-slate-800 hover:bg-red-500/20 text-slate-300 hover:text-red-400 font-bold rounded-xl transition-all border border-transparent hover:border-red-500/30 whitespace-nowrap"
            >
              Disconnect
            </button>
          )}
        </div>

        {userSettings.telegramUserId && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 border-t border-blue-500/20 pt-6">
            <div className="bg-slate-900/50 p-4 rounded-2xl border border-slate-800/50 flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Download className="w-4 h-4 text-emerald-400" />
                  <span className="text-sm font-bold text-white">Pull from Bot</span>
                </div>
                <p className="text-xs text-slate-400 mb-4">Overwrite site settings with bot data.</p>
              </div>
              <button 
                onClick={handleSyncFromBot}
                disabled={isSyncing}
                className="w-full py-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/20 rounded-xl text-xs font-bold transition-all disabled:opacity-50"
              >
                {isSyncing ? 'Syncing...' : 'PULL DATA'}
              </button>
            </div>

            <div className="bg-slate-900/50 p-4 rounded-2xl border border-slate-800/50 flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <Upload className="w-4 h-4 text-orange-400" />
                  <span className="text-sm font-bold text-white">Push to Bot</span>
                </div>
                <p className="text-xs text-slate-400 mb-4">Overwrite bot settings with site data.</p>
              </div>
              <button 
                onClick={handleSyncToBot}
                disabled={isSyncing}
                className="w-full py-2 bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border border-orange-500/20 rounded-xl text-xs font-bold transition-all disabled:opacity-50"
              >
                {isSyncing ? 'Syncing...' : 'PUSH DATA'}
              </button>
            </div>

            <div className="bg-slate-900/50 p-4 rounded-2xl border border-slate-800/50 flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <RefreshCw className="w-4 h-4 text-blue-400" />
                  <span className="text-sm font-bold text-white">Auto-Sync</span>
                </div>
                <p className="text-xs text-slate-400 mb-4">Automatically push changes to bot.</p>
              </div>
              <button 
                onClick={toggleAutoSync}
                className={cn(
                  "w-full py-2 border rounded-xl text-xs font-bold transition-all",
                  userSettings.autoSyncTelegram 
                    ? "bg-blue-500/20 border-blue-500/30 text-blue-400" 
                    : "bg-slate-800 border-slate-700 text-slate-400 hover:bg-slate-700"
                )}
              >
                {userSettings.autoSyncTelegram ? 'ENABLED' : 'DISABLED'}
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="bg-slate-900 border border-slate-800 rounded-3xl p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="p-2 bg-slate-800 rounded-xl">
            <Settings className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Особисті фільтри</h2>
            <p className="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">Налаштування капіталу та спреду</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6">
            <div>
              <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">Робочий капітал (UAH)</label>
              <div className="flex items-center gap-4 mb-3">
                <div className="flex-1">
                  <span className="text-[10px] text-slate-500 uppercase font-bold mb-1 block">Min</span>
                  <input
                    type="number"
                    min="0"
                    value={userSettings.minCapital}
                    onChange={(e) => onUserChange('minCapital', parseInt(e.target.value) || 0)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm text-white focus:border-emerald-500 outline-none transition-all"
                  />
                </div>
                <div className="flex-1">
                  <span className="text-[10px] text-slate-500 uppercase font-bold mb-1 block">Max</span>
                  <input
                    type="number"
                    min="0"
                    value={userSettings.maxCapital}
                    onChange={(e) => onUserChange('maxCapital', parseInt(e.target.value) || 0)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm text-white focus:border-emerald-500 outline-none transition-all"
                  />
                </div>
              </div>
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex justify-between items-center">
                <span className="text-xs font-bold text-emerald-500/80">Середня сума угоди:</span>
                <span className="text-sm font-black text-emerald-400">{averageCapital.toLocaleString()} ₴</span>
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">Мінімальний спред (%)</label>
              <div className="grid grid-cols-4 gap-2 mb-3">
                {[0.3, 0.5, 1.0, 2.0].map(val => (
                  <button
                    key={val}
                    onClick={() => onUserChange('minSpread', val)}
                    className={cn(
                      "py-2.5 rounded-xl text-xs font-bold transition-all border",
                      userSettings.minSpread === val
                        ? "bg-emerald-500 border-emerald-400 text-slate-950 shadow-lg shadow-emerald-500/20"
                        : "bg-slate-800 border-slate-700 text-slate-400 hover:bg-slate-700"
                    )}
                  >
                    {val}%
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-3 p-1 bg-slate-950 border border-slate-800 rounded-xl focus-within:border-emerald-500 transition-all">
                <span className="text-xs font-bold text-slate-500 pl-3 uppercase tracking-widest">Ручний ввід:</span>
                <input
                  type="number"
                  step="0.1"
                  value={userSettings.minSpread}
                  onChange={(e) => onUserChange('minSpread', parseFloat(e.target.value) || 0)}
                  className="flex-1 bg-transparent border-none text-sm font-bold text-white focus:ring-0 outline-none py-2"
                />
                <span className="pr-4 text-slate-500 font-bold">%</span>
              </div>
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">Цільові Банки</label>
            <div className="space-y-2">
              <BankToggle label="Monobank" active={(userSettings?.banks || []).includes("43")} onClick={() => toggleBank("43")} />
              <BankToggle label="PrivatBank" active={(userSettings?.banks || []).includes("14")} onClick={() => toggleBank("14")} />
              <BankToggle label="PUMB" active={(userSettings?.banks || []).includes("64")} onClick={() => toggleBank("64")} />
              <BankToggle label="A-Bank" active={(userSettings?.banks || []).includes("48")} onClick={() => toggleBank("48")} />
            </div>
          </div>
        </div>
      </section>

      <section className="bg-slate-900 border border-slate-800 rounded-3xl p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="p-2 bg-slate-800 rounded-xl">
            <Filter className="w-5 h-5 text-purple-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Фільтри Мерчантів</h2>
            <p className="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">Відсіювання ненадійних контрагентів</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">Мін. угод (за місяць)</label>
            <input
              type="number"
              min="0"
              value={userSettings.merchantFilters?.minOrders || 0}
              onChange={(e) => handleMerchantFilterChange('minOrders', parseInt(e.target.value) || 0)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm font-bold text-white focus:border-emerald-500 outline-none"
              placeholder="0 для вимкнення"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-500 mb-2 block uppercase tracking-tight">Мін. відсоток успішних (%)</label>
            <input
              type="number"
              min="0"
              max="100"
              step="0.1"
              value={userSettings.merchantFilters?.minRate || 0}
              onChange={(e) => handleMerchantFilterChange('minRate', parseFloat(e.target.value) || 0)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm font-bold text-white focus:border-emerald-500 outline-none"
              placeholder="0 для вимкнення"
            />
          </div>
        </div>
      </section>

      {isAdmin && (
        <section className="bg-slate-900 border border-orange-500/20 rounded-3xl p-6 relative overflow-hidden">
          <div className="absolute top-0 right-0 w-32 h-32 bg-orange-500/5 rounded-full blur-3xl -mr-10 -mt-10 pointer-events-none"></div>

          <div className="flex items-center gap-3 mb-8">
            <div className="p-2 bg-orange-500/10 rounded-xl border border-orange-500/20">
              <ShieldAlert className="w-5 h-5 text-orange-400" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-orange-400">Системні налаштування</h2>
              <p className="text-[10px] uppercase tracking-widest text-orange-500/60 font-semibold">Впливають на поведінку всього сканера</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            <AdminSettingCard
              icon={<Shield className="w-4 h-4 text-red-400" />}
              label="Рівень антифроду"
              subLabel="risk_mode"
            >
              <select
                value={globalSettings.riskMode}
                onChange={(e) => onGlobalChange('riskMode', e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 outline-none"
              >
                <option value="RELAXED">RELAXED</option>
                <option value="WARNING">WARNING</option>
                <option value="STRICT">STRICT</option>
              </select>
            </AdminSettingCard>

            <AdminSettingCard
              icon={<Bot className="w-4 h-4 text-blue-400" />}
              label="Поріг балів ботів"
              subLabel="behavior_alert_score"
            >
              <input
                type="number"
                value={globalSettings.behaviorAlertScore}
                onChange={(e) => onGlobalChange('behaviorAlertScore', parseInt(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 outline-none"
              />
            </AdminSettingCard>

            <AdminSettingCard
              icon={<Zap className="w-4 h-4 text-yellow-400" />}
              label="Аномальна швидкість (угод/год)"
              subLabel="velocity_spike_per_hour"
            >
              <input
                type="number"
                step="0.1"
                value={globalSettings.velocitySpikePerHour}
                onChange={(e) => onGlobalChange('velocitySpikePerHour', parseFloat(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 outline-none"
              />
            </AdminSettingCard>

            <AdminSettingCard
              icon={<Pin className="w-4 h-4 text-red-500" />}
              label="Липкі ліміти (циклів)"
              subLabel="sticky_min_chain"
            >
              <input
                type="number"
                value={globalSettings.stickyMinChain}
                onChange={(e) => onGlobalChange('stickyMinChain', parseInt(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 outline-none"
              />
            </AdminSettingCard>

            <AdminSettingCard
              icon={<MessageSquare className="w-4 h-4 text-slate-300" />}
              label="Кеш відгуків (годин)"
              subLabel="review_ttl_hours"
            >
              <input
                type="number"
                step="0.1"
                value={globalSettings.reviewTtlHours}
                onChange={(e) => onGlobalChange('reviewTtlHours', parseFloat(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 outline-none"
              />
            </AdminSettingCard>

            <AdminSettingCard
              icon={<Bell className="w-4 h-4 text-yellow-500" />}
              label="Макс. алертів за цикл"
              subLabel="max_alerts_per_cycle"
            >
              <input
                type="number"
                value={globalSettings.maxAlertsPerCycle}
                onChange={(e) => onGlobalChange('maxAlertsPerCycle', parseInt(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 outline-none"
              />
            </AdminSettingCard>
          </div>
        </section>
      )}
    </div>
  );
}

function AdminSettingCard({ icon, label, subLabel, children }: any) {
  return (
    <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <span className="text-sm font-bold text-white">{label}</span>
      </div>
      <div className="text-[10px] font-mono text-slate-500 mb-3 pl-6">
        └ {subLabel}
      </div>
      {children}
    </div>
  );
}

function BankToggle({ label, active, onClick }: any) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "flex items-center justify-between p-3 rounded-2xl border transition-all cursor-pointer",
        active ? "bg-emerald-500/10 border-emerald-500/30" : "bg-slate-950 border-slate-800 hover:border-slate-700"
      )}
    >
      <span className={cn("text-xs font-bold", active ? "text-emerald-400" : "text-slate-400")}>{label}</span>
      <div className={cn(
        "w-4 h-4 rounded-full border-2 flex items-center justify-center",
        active ? "bg-emerald-500 border-emerald-400" : "border-slate-700"
      )}>
        {active && <CheckCircle2 className="w-3 h-3 text-slate-950" />}
      </div>
    </div>
  );
}
