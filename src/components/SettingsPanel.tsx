import React, {useEffect, useRef, useState} from 'react';
import {
  Settings, ShieldAlert, CheckCircle2, Filter, Shield, Bot, Zap,
  Pin, MessageSquare, Bell, Send, Download, Upload, RefreshCw,
  Volume2, VolumeX, DollarSign, Percent, Building2, Link2, Unlink,
  ArrowDownToLine, Trash2, AlertTriangle, Users, Database
} from 'lucide-react';
import { cn } from '../lib/utils';
import { motion } from 'motion/react';
import { useAppStore } from '../store';
import { api } from '../services/api';
import { toast } from 'sonner';
import { doc, setDoc } from 'firebase/firestore';
import { db, auth } from '../firebase';
import { GlobalSettings } from '../types';

export default function SettingsPanel() {
  const { userSettings, isAdmin } = useAppStore();
  const isFirstRender = useRef(true);
  const isUpdatingFromBot = useRef(false); // 🔴 ОСЬ ЦЕЙ ЩИТ РЯТУЄ ВІД ЦИКЛІВ!

  // 1. PUSH: САЙТ -> БОТ (Коли юзер крутить повзунки на сайті)
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    if (!userSettings.autoSyncTelegram || !userSettings.telegramUserId) return;
    if (isUpdatingFromBot.current) {
      isUpdatingFromBot.current = false;
      return;
    }

    const timeoutId = setTimeout(async () => {
      try {
        const prefs = userSettings.syncPreferences || { capital: true, spread: true, banks: true, apiKeys: false };

        const botData = await api.syncTelegram(userSettings.telegramUserId!);
        const botSettings = botData?.settings || {};

        const payloadToBot = {
          ...userSettings,
          maxCapital: prefs.capital ? userSettings.maxCapital : (botSettings.maxCapital || userSettings.maxCapital),
          minSpread: prefs.spread ? userSettings.minSpread : (botSettings.minSpread || userSettings.minSpread),
          banks: prefs.banks ? userSettings.banks : (botSettings.banks || userSettings.banks),
          // apiKeys: якщо дозволено — відправляємо, інакше залишаємо те що є в боті
          apiKeys: prefs.apiKeys ? userSettings.apiKeys : (botSettings.apiKeys || userSettings.apiKeys),
        };

        await api.updateTelegramSettings(userSettings.telegramUserId, payloadToBot);

        if (auth.currentUser) {
          await setDoc(doc(db, 'users', auth.currentUser.uid), userSettings, { merge: true });
        }

        toast.success('Налаштування відправлено в бот 📤', {
          icon: <RefreshCw className="w-4 h-4 text-blue-400" />
        });
      } catch (error) {
        toast.error('Помилка авто-синхронізації');
      }
    }, 1000);

    return () => clearTimeout(timeoutId);

  },[
    userSettings.minCapital,
    userSettings.maxCapital,
    userSettings.minSpread,
    userSettings.banks,
    userSettings.merchantFilters,
    userSettings.autoSyncTelegram,
    userSettings.apiKeys,
    userSettings.syncPreferences
  ]);

  // 2. PULL: БОТ -> САЙТ (Опитування кожні 5 секунд)
  useEffect(() => {
    if (!userSettings.telegramUserId || !userSettings.autoSyncTelegram) return;

    const checkBotSettings = async () => {
      try {
        const botData = await api.syncTelegram(userSettings.telegramUserId!);
        if (botData) {
          const current = useAppStore.getState().userSettings;
          const botSettings = botData.settings;
          const prefs = current.syncPreferences || { capital: true, spread: true, banks: true, apiKeys: false };

          let hasChanges = false;
          const newSettings = { ...current };

          if (prefs.capital && botSettings.maxCapital !== current.maxCapital) {
            newSettings.maxCapital = botSettings.maxCapital;
            hasChanges = true;
          }
          if (prefs.spread && botSettings.minSpread !== current.minSpread) {
            newSettings.minSpread = botSettings.minSpread;
            hasChanges = true;
          }
          if (prefs.banks && JSON.stringify(botSettings.banks) !== JSON.stringify(current.banks)) {
            newSettings.banks = botSettings.banks;
            hasChanges = true;
          }
          // Мердж ключів бірж: не затираємо існуючі — лише додаємо відсутні з бота
          if (prefs.apiKeys && botSettings.apiKeys) {
            const mergedKeys = { ...(current.apiKeys || {}) };
            let keysChanged = false;
            for (const [exchange, keys] of Object.entries(botSettings.apiKeys as Record<string, any>)) {
              if (!mergedKeys[exchange]) {
                mergedKeys[exchange] = keys;
                keysChanged = true;
              }
            }
            if (keysChanged) {
              newSettings.apiKeys = mergedKeys;
              hasChanges = true;
            }
          }

          if (hasChanges) {
            isUpdatingFromBot.current = true;
            useAppStore.getState().setUserSettings(newSettings);

            if (auth.currentUser) {
              await setDoc(doc(db, 'users', auth.currentUser.uid), newSettings, { merge: true });
            }
            toast.info('🤖 Налаштування оновлено з Телеграму 📥');
          }
        }
      } catch (e) {
        // Ignore network errors
      }
    };

    const intervalId = setInterval(checkBotSettings, 5000);
    checkBotSettings();

    return () => clearInterval(intervalId);
  }, [userSettings.telegramUserId, userSettings.autoSyncTelegram, userSettings.syncPreferences]);


  return (
    <div className="space-y-8">
      <TelegramSync />
      <SoundSettings />
      <UserFilters />
      {isAdmin && <AdminSettings />}
    </div>
  );
}

function SoundSettings() {
  const { userSettings, setUserSettings } = useAppStore();

  const toggleSound = async () => {
    const newVal = !userSettings.soundEnabled;
    setUserSettings({ ...userSettings, soundEnabled: newVal });
    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), { soundEnabled: newVal }, { merge: true });
    }
  };

  const changeVolume = async (val: number) => {
    setUserSettings({ ...userSettings, soundVolume: val });
    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), { soundVolume: val }, { merge: true });
    }
  };

  return (
    <section className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6">
      <div className="flex items-center gap-4 mb-6">
        <div className="p-3 bg-indigo-500/20 rounded-xl">
          <Volume2 className="w-6 h-6 text-indigo-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-white">Audio Notifications</h2>
          <p className="text-sm text-slate-400">Manage sound alerts for new opportunities</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-bold text-slate-300">Enable Sounds</span>
            <button
              onClick={toggleSound} // <--- ЗМІНЕНО ТУТ
              className={cn(
                "w-12 h-6 rounded-full transition-colors relative",
                userSettings.soundEnabled ? "bg-indigo-500" : "bg-slate-700"
              )}
            >
              <motion.div
                className="w-4 h-4 bg-white rounded-full absolute top-1"
                animate={{ left: userSettings.soundEnabled ? '26px' : '4px' }}
                transition={{ type: "spring", stiffness: 500, damping: 30 }}
              />
            </button>
          </div>
          <p className="text-xs text-slate-500">Play a sound when a high-spread opportunity is found.</p>
        </div>

        <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm font-bold text-slate-300">Volume</span>
            <span className="text-xs font-medium text-indigo-400 bg-indigo-500/10 px-2 py-1 rounded-md">
              {Math.round((userSettings.soundVolume || 0.5) * 100)}%
            </span>
          </div>
          <div className="flex items-center gap-3">
            <VolumeX className="w-4 h-4 text-slate-500" />
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={userSettings.soundVolume || 0.5}
              onChange={(e) => changeVolume(parseFloat(e.target.value))} // <--- ЗМІНЕНО ТУТ
              className="w-full accent-indigo-500"
              disabled={!userSettings.soundEnabled}
            />
            <Volume2 className="w-4 h-4 text-slate-400" />
          </div>
        </div>
      </div>
    </section>
  );
}

function TelegramSync() {
  const { userSettings, setUserSettings, setIsAdmin } = useAppStore();
  const [tgInput, setTgInput] = useState('');
  const[isConnecting, setIsConnecting] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  // Дефолтні налаштування синхронізації (apiKeys вимкнено за замовчуванням — безпечніше)
  const prefs = userSettings.syncPreferences || { capital: true, spread: true, banks: true, apiKeys: false };

  const togglePref = async (key: keyof typeof prefs) => {
    const newPrefs = { ...prefs, [key]: !prefs[key] };
    const newSettings = { ...userSettings, syncPreferences: newPrefs };
    setUserSettings(newSettings);
    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), { syncPreferences: newPrefs }, { merge: true });
    }
  };

  const handleConnect = async () => {
    if (!tgInput) return;
    setIsConnecting(true);
    try {
      const botData = await api.syncTelegram(tgInput);
      if (botData) {
        const newApiKeys = { ...(userSettings.apiKeys || {}) };
        botData.keys.forEach((k: string) => {
          if (!newApiKeys[k]) {
            newApiKeys[k] = { key: 'imported_from_bot', secret: 'imported_from_bot' };
          }
        });

        const newSettings = {
          ...userSettings,
          ...botData.settings,
          apiKeys: newApiKeys,
          telegramUserId: tgInput,
          isTelegramAdmin: botData.isAdmin,
          autoSyncTelegram: true
        };

        setUserSettings(newSettings);
        setIsAdmin(botData.isAdmin);
        if (auth.currentUser) {
          await setDoc(doc(db, 'users', auth.currentUser.uid), newSettings, { merge: true });
        }
        toast.success('Successfully connected to Telegram Bot!');
      }
    } catch (error) {
      toast.error('Failed to connect to Telegram Bot');
    } finally {
      setIsConnecting(false);
    }
  };

  const handleDisconnectTelegram = async () => {
    const newSettings = { ...userSettings };
    delete newSettings.telegramUserId;
    delete newSettings.isTelegramAdmin;
    delete newSettings.autoSyncTelegram;
    setUserSettings(newSettings);
    setIsAdmin(false);
    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), { telegramUserId: null, isTelegramAdmin: null, autoSyncTelegram: null }, { merge: true });
    }
    toast.info('Disconnected from Telegram Bot');
  };

  const handleSyncFromBot = async () => {
    if (!userSettings.telegramUserId) return;
    setIsSyncing(true);
    try {
      const botData = await api.syncTelegram(userSettings.telegramUserId);
      if (botData) {
        const botSettings = botData.settings;

        // Мердж ключів бірж: не затираємо існуючі — лише додаємо відсутні
        const mergedKeys = { ...(userSettings.apiKeys || {}) };
        if (prefs.apiKeys && botSettings.apiKeys) {
          for (const [exchange, keys] of Object.entries(botSettings.apiKeys as Record<string, any>)) {
            if (!mergedKeys[exchange]) mergedKeys[exchange] = keys;
          }
        }

        const newSettings = {
          ...userSettings,
          maxCapital: prefs.capital ? botSettings.maxCapital : userSettings.maxCapital,
          minSpread: prefs.spread ? botSettings.minSpread : userSettings.minSpread,
          banks: prefs.banks ? botSettings.banks : userSettings.banks,
          apiKeys: prefs.apiKeys ? mergedKeys : userSettings.apiKeys,
          isTelegramAdmin: botData.isAdmin
        };
        setUserSettings(newSettings);
        setIsAdmin(botData.isAdmin);
        if (auth.currentUser) {
          await setDoc(doc(db, 'users', auth.currentUser.uid), newSettings, { merge: true });
        }
        toast.success('Налаштування стягнуто з бота');
      }
    } catch (error) {
      toast.error('Помилка стягування налаштувань');
    } finally {
      setIsSyncing(false);
    }
  };

  const handleSyncToBot = async () => {
    if (!userSettings.telegramUserId) return;
    setIsSyncing(true);
    try {
      const botData = await api.syncTelegram(userSettings.telegramUserId);
      const botSettings = botData?.settings || {};

      const payloadToBot = {
        ...userSettings,
        maxCapital: prefs.capital ? userSettings.maxCapital : (botSettings.maxCapital || userSettings.maxCapital),
        minSpread: prefs.spread ? userSettings.minSpread : (botSettings.minSpread || userSettings.minSpread),
        banks: prefs.banks ? userSettings.banks : (botSettings.banks || userSettings.banks),
        apiKeys: prefs.apiKeys ? userSettings.apiKeys : (botSettings.apiKeys || userSettings.apiKeys),
      };

      await api.updateTelegramSettings(userSettings.telegramUserId, payloadToBot);
      toast.success('Налаштування відправлено в бот');
    } catch (error) {
      toast.error('Помилка відправки налаштувань');
    } finally {
      setIsSyncing(false);
    }
  };

  const toggleAutoSync = () => {
    const newVal = !userSettings.autoSyncTelegram;
    setUserSettings({ ...userSettings, autoSyncTelegram: newVal });
    if (auth.currentUser) {
      setDoc(doc(db, 'users', auth.currentUser.uid), { autoSyncTelegram: newVal }, { merge: true });
    }
  };

  return (
    <section className="bg-blue-500/10 border border-blue-500/20 rounded-3xl p-6">
      {/* ... Верхня частина (input для ID) залишається БЕЗ ЗМІН ... */}
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
              className="bg-slate-950 border border-blue-500/30 rounded-xl px-4 py-2 text-sm text-white focus:border-blue-500 focus:ring-2 focus:ring-blue-500/50 outline-none w-full md:w-48 transition-all"
            />
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.95 }}
              onClick={handleConnect}
              disabled={isConnecting || !tgInput}
              className="flex items-center gap-2 px-4 py-2 bg-blue-500 hover:bg-blue-400 text-slate-950 font-bold rounded-xl transition-all disabled:opacity-50 whitespace-nowrap focus:ring-2 focus:ring-blue-500/50 outline-none"
            >
              <Link2 className="w-4 h-4" />
              {isConnecting ? 'Connecting...' : 'Connect'}
            </motion.button>
          </div>
        ) : (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.95 }}
            onClick={handleDisconnectTelegram}
            className="flex items-center gap-2 px-4 py-2 bg-red-500/20 text-red-400 font-bold rounded-xl transition-all border border-red-500/30 hover:bg-red-500/30 whitespace-nowrap focus:ring-2 focus:ring-red-500/50 outline-none"
          >
            <Unlink className="w-4 h-4" />
            Disconnect
          </motion.button>
        )}
      </div>

      {userSettings.telegramUserId && (
        <div className="flex items-center gap-2 text-sm text-blue-400 mb-4 mt-2">
          <CheckCircle2 className="w-4 h-4" />
          Connected · ID: {userSettings.telegramUserId}
        </div>
      )}

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
            <motion.button
              whileHover={{ scale: userSettings.autoSyncTelegram ? 1 : 1.02 }}
              whileTap={{ scale: userSettings.autoSyncTelegram ? 1 : 0.95 }}
              onClick={handleSyncFromBot}
              disabled={isSyncing || !!userSettings.autoSyncTelegram}
              title={userSettings.autoSyncTelegram ? 'Вимкни Auto-Sync щоб використовувати ручне керування' : undefined}
              className={cn(
                "w-full py-2 border rounded-xl text-xs font-bold transition-all focus:ring-2 outline-none",
                userSettings.autoSyncTelegram
                  ? "bg-slate-800/50 border-slate-700/50 text-slate-600 cursor-not-allowed"
                  : "bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border-emerald-500/20 focus:ring-emerald-500/50"
              )}
            >
              {userSettings.autoSyncTelegram ? '🔒 AUTO-SYNC ON' : isSyncing ? 'Syncing...' : 'PULL DATA'}
            </motion.button>
          </div>

          <div className="bg-slate-900/50 p-4 rounded-2xl border border-slate-800/50 flex flex-col justify-between">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Upload className="w-4 h-4 text-orange-400" />
                <span className="text-sm font-bold text-white">Push to Bot</span>
              </div>
              <p className="text-xs text-slate-400 mb-4">Overwrite bot settings with site data.</p>
            </div>
            <motion.button
              whileHover={{ scale: userSettings.autoSyncTelegram ? 1 : 1.02 }}
              whileTap={{ scale: userSettings.autoSyncTelegram ? 1 : 0.95 }}
              onClick={handleSyncToBot}
              disabled={isSyncing || !!userSettings.autoSyncTelegram}
              title={userSettings.autoSyncTelegram ? 'Вимкни Auto-Sync щоб використовувати ручне керування' : undefined}
              className={cn(
                "w-full py-2 border rounded-xl text-xs font-bold transition-all focus:ring-2 outline-none",
                userSettings.autoSyncTelegram
                  ? "bg-slate-800/50 border-slate-700/50 text-slate-600 cursor-not-allowed"
                  : "bg-orange-500/10 hover:bg-orange-500/20 text-orange-400 border-orange-500/20 focus:ring-orange-500/50"
              )}
            >
              {userSettings.autoSyncTelegram ? '🔒 AUTO-SYNC ON' : isSyncing ? 'Syncing...' : 'PUSH DATA'}
            </motion.button>
          </div>

          <div className="bg-slate-900/50 p-4 rounded-2xl border border-slate-800/50 flex flex-col justify-between">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <RefreshCw className={cn("w-4 h-4 text-blue-400 transition-all", userSettings.autoSyncTelegram && "animate-spin")} />
                <span className="text-sm font-bold text-white">Auto-Sync</span>
              </div>
              <p className="text-xs text-slate-400 mb-4">Automatically push changes to bot.</p>
            </div>
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.95 }}
              onClick={toggleAutoSync}
              className={cn(
                "w-full py-2 border rounded-xl text-xs font-bold transition-all focus:ring-2 focus:ring-blue-500/50 outline-none",
                userSettings.autoSyncTelegram
                  ? "bg-blue-500/20 border-blue-500/30 text-blue-400"
                  : "bg-slate-800 border-slate-700 text-slate-400 hover:bg-slate-700"
              )}
            >
              {userSettings.autoSyncTelegram ? 'ENABLED' : 'DISABLED'}
            </motion.button>
          </div>

          {/* НОВИЙ БЛОК: Вибір даних для синхронізації */}
          <div className="col-span-1 md:col-span-3 mt-2 p-4 bg-slate-950/50 rounded-2xl border border-blue-500/10">
            <h3 className="text-sm font-bold text-slate-300 mb-3 flex items-center gap-2">
              <Filter className="w-4 h-4 text-blue-400" />
              Що саме синхронізувати:
            </h3>
            <div className="flex flex-wrap gap-6">
               <label className="flex items-center gap-2 cursor-pointer group">
                  <div className={cn("w-4 h-4 rounded border flex items-center justify-center transition-colors", prefs.capital ? "bg-blue-500 border-blue-500" : "border-slate-600 group-hover:border-blue-500/50")}>
                    {prefs.capital && <CheckCircle2 className="w-3 h-3 text-slate-950" />}
                  </div>
                  <input type="checkbox" checked={prefs.capital} onChange={() => togglePref('capital')} className="hidden" />
                  <span className="text-xs font-bold text-slate-400 group-hover:text-slate-300 transition-colors">Робочий Капітал</span>
               </label>

               <label className="flex items-center gap-2 cursor-pointer group">
                  <div className={cn("w-4 h-4 rounded border flex items-center justify-center transition-colors", prefs.spread ? "bg-blue-500 border-blue-500" : "border-slate-600 group-hover:border-blue-500/50")}>
                    {prefs.spread && <CheckCircle2 className="w-3 h-3 text-slate-950" />}
                  </div>
                  <input type="checkbox" checked={prefs.spread} onChange={() => togglePref('spread')} className="hidden" />
                  <span className="text-xs font-bold text-slate-400 group-hover:text-slate-300 transition-colors">Мінімальний Спред</span>
               </label>

               <label className="flex items-center gap-2 cursor-pointer group">
                  <div className={cn("w-4 h-4 rounded border flex items-center justify-center transition-colors", prefs.banks ? "bg-blue-500 border-blue-500" : "border-slate-600 group-hover:border-blue-500/50")}>
                    {prefs.banks && <CheckCircle2 className="w-3 h-3 text-slate-950" />}
                  </div>
                  <input type="checkbox" checked={prefs.banks} onChange={() => togglePref('banks')} className="hidden" />
                  <span className="text-xs font-bold text-slate-400 group-hover:text-slate-300 transition-colors">Цільові Банки</span>
               </label>

               <label className="flex items-center gap-2 cursor-pointer group">
                  <div className={cn("w-4 h-4 rounded border flex items-center justify-center transition-colors", prefs.apiKeys ? "bg-amber-500 border-amber-500" : "border-slate-600 group-hover:border-amber-500/50")}>
                    {prefs.apiKeys && <CheckCircle2 className="w-3 h-3 text-slate-950" />}
                  </div>
                  <input type="checkbox" checked={prefs.apiKeys} onChange={() => togglePref('apiKeys')} className="hidden" />
                  <span className="text-xs font-bold text-slate-400 group-hover:text-slate-300 transition-colors">
                    Ключі Бірж
                    <span className="ml-1.5 text-[10px] text-amber-500/70 font-semibold uppercase tracking-wider">⚠ sensitive</span>
                  </span>
               </label>
            </div>
            <p className="text-[10px] text-slate-500 mt-3 font-medium uppercase tracking-wider">
              Зняті галочки назавжди роз'єднують цей параметр між сайтом та телеграмом.
            </p>
          </div>

        </div>
      )}
    </section>
  );
}

function UserFilters() {
  const { userSettings, setUserSettings } = useAppStore();

  const toggleBank = (code: string) => {
    const newBanks = userSettings.banks.includes(code)
      ? userSettings.banks.filter(b => b !== code)
      : [...userSettings.banks, code];
    setUserSettings({ ...userSettings, banks: newBanks });
  };

  const handleMerchantFilterChange = (key: 'minOrders' | 'minRate', value: number) => {
    const currentFilters = userSettings.merchantFilters || { minOrders: 0, minRate: 0 };
    setUserSettings({ ...userSettings, merchantFilters: { ...currentFilters, [key]: value } });
  };

  const averageCapital = Math.floor((userSettings.minCapital + userSettings.maxCapital) / 2);

  return (
    <>
      <section className="bg-slate-900 border border-slate-800 rounded-3xl p-6">
        <div className="flex items-center gap-3 mb-6">
          <div className="p-2 bg-slate-800 rounded-xl">
            <Settings className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Особисті фільтри</h2>
            <p className="text-xs uppercase tracking-widest text-slate-400 font-semibold">Налаштування капіталу та спреду</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6">
            <div>
              <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">Робочий капітал (UAH)</label>
              <div className="flex items-center gap-4 mb-3">
                <div className="flex-1">
                  <span className="text-xs text-slate-400 uppercase font-bold mb-1 block">Min</span>
                  <input
                    type="number"
                    min="0"
                    value={userSettings.minCapital}
                    onChange={(e) => setUserSettings({ ...userSettings, minCapital: parseInt(e.target.value) || 0 })}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all tabular-nums"
                  />
                </div>
                <div className="flex-1">
                  <span className="text-xs text-slate-400 uppercase font-bold mb-1 block">Max</span>
                  <input
                    type="number"
                    min="0"
                    value={userSettings.maxCapital}
                    onChange={(e) => setUserSettings({ ...userSettings, maxCapital: parseInt(e.target.value) || 0 })}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all tabular-nums"
                  />
                </div>
              </div>
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl flex justify-between items-center">
                <span className="text-xs font-bold text-emerald-500/80">Середня сума угоди:</span>
                <span className="text-sm font-black text-emerald-400 tabular-nums">{averageCapital.toLocaleString()} ₴</span>
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">Мінімальний спред (%)</label>
              <div className="grid grid-cols-4 gap-2 mb-3">
                {[0.3, 0.5, 1.0, 2.0].map(val => (
                  <motion.button
                    whileHover={{ scale: 1.05 }}
                    whileTap={{ scale: 0.95 }}
                    key={val}
                    onClick={() => setUserSettings({ ...userSettings, minSpread: val })}
                    className={cn(
                      "py-2.5 rounded-xl text-xs font-bold transition-all border focus:ring-2 focus:ring-emerald-500/50 outline-none tabular-nums",
                      userSettings.minSpread === val
                        ? "bg-emerald-500 border-emerald-400 text-slate-950 shadow-lg shadow-emerald-500/20"
                        : "bg-slate-800 border-slate-700 text-slate-400 hover:bg-slate-700"
                    )}
                  >
                    {val}%
                  </motion.button>
                ))}
              </div>
              <div className="flex items-center gap-3 p-1 bg-slate-950 border border-slate-800 rounded-xl focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-500/50 transition-all">
                <span className="text-xs font-bold text-slate-400 pl-3 uppercase tracking-widest">Ручний ввід:</span>
                <input
                  type="number"
                  step="0.1"
                  value={userSettings.minSpread}
                  onChange={(e) => setUserSettings({ ...userSettings, minSpread: parseFloat(e.target.value) || 0 })}
                  className="flex-1 bg-transparent border-none text-sm font-bold text-white focus:ring-0 outline-none py-2 tabular-nums"
                />
                <span className="pr-4 text-slate-400 font-bold">%</span>
              </div>
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">Цільові Банки</label>
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
            <p className="text-xs uppercase tracking-widest text-slate-400 font-semibold">Відсіювання ненадійних контрагентів</p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">Мін. угод (за місяць)</label>
            <input
              type="number"
              min="0"
              value={userSettings.merchantFilters?.minOrders || 0}
              onChange={(e) => handleMerchantFilterChange('minOrders', parseInt(e.target.value) || 0)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm font-bold text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all tabular-nums"
              placeholder="0 для вимкнення"
            />
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-400 mb-2 block uppercase tracking-tight">Мін. відсоток успішних (%)</label>
            <input
              type="number"
              min="0"
              max="100"
              step="0.1"
              value={userSettings.merchantFilters?.minRate || 0}
              onChange={(e) => handleMerchantFilterChange('minRate', parseFloat(e.target.value) || 0)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-3 text-sm font-bold text-white focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/50 outline-none transition-all tabular-nums"
              placeholder="0 для вимкнення"
            />
          </div>
        </div>
      </section>
    </>
  );
}

function AdminSettings() {
  const { globalSettings, setGlobalSettings } = useAppStore();

  const handleGlobalChange = async (key: keyof GlobalSettings, value: any) => {
    const newSettings = { ...globalSettings, [key]: value };
    setGlobalSettings(newSettings);
    await api.updateGlobalSettings(newSettings);
  };

  return (
    <section className="bg-slate-900 border border-orange-500/20 rounded-3xl p-6 relative overflow-hidden">
      <div className="absolute top-0 right-0 w-32 h-32 bg-orange-500/5 rounded-full blur-3xl -mr-10 -mt-10 pointer-events-none"></div>

      <div className="flex items-center gap-3 mb-8">
        <div className="p-2 bg-orange-500/10 rounded-xl border border-orange-500/20">
          <ShieldAlert className="w-5 h-5 text-orange-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-orange-400">Системні налаштування</h2>
          <p className="text-xs uppercase tracking-widest text-orange-500/60 font-semibold">Впливають на поведінку всього сканера</p>
        </div>
      </div>

      <motion.div
        className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
        initial="hidden"
        animate="visible"
        variants={{ hidden: {}, visible: { transition: { staggerChildren: 0.08 } } }}
      >
        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Shield className="w-4 h-4 text-red-400" />}
          label="Рівень антифроду"
          subLabel="risk_mode"
        >
          <select
            value={globalSettings.riskMode}
            onChange={(e) => handleGlobalChange('riskMode', e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all"
          >
            <option value="RELAXED">RELAXED</option>
            <option value="WARNING">WARNING</option>
            <option value="STRICT">STRICT</option>
          </select>
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Bot className="w-4 h-4 text-blue-400" />}
          label="Поріг балів ботів"
          subLabel="behavior_alert_score"
        >
          <input
            type="number"
            value={globalSettings.behaviorAlertScore}
            onChange={(e) => handleGlobalChange('behaviorAlertScore', parseInt(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Zap className="w-4 h-4 text-yellow-400" />}
          label="Аномальна швидкість (угод/год)"
          subLabel="velocity_spike_per_hour"
        >
          <input
            type="number"
            step="0.1"
            value={globalSettings.velocitySpikePerHour}
            onChange={(e) => handleGlobalChange('velocitySpikePerHour', parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Pin className="w-4 h-4 text-red-500" />}
          label="Липкі ліміти (циклів)"
          subLabel="sticky_min_chain"
        >
          <input
            type="number"
            value={globalSettings.stickyMinChain}
            onChange={(e) => handleGlobalChange('stickyMinChain', parseInt(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<MessageSquare className="w-4 h-4 text-slate-300" />}
          label="Кеш відгуків (годин)"
          subLabel="review_ttl_hours"
        >
          <input
            type="number"
            step="0.1"
            value={globalSettings.reviewTtlHours}
            onChange={(e) => handleGlobalChange('reviewTtlHours', parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Bell className="w-4 h-4 text-yellow-500" />}
          label="Макс. алертів за цикл"
          subLabel="max_alerts_per_cycle"
        >
          <input
            type="number"
            value={globalSettings.maxAlertsPerCycle}
            onChange={(e) => handleGlobalChange('maxAlertsPerCycle', parseInt(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>
      </motion.div>
    </section>
  );
}

function AdminSettingCard({ icon, label, subLabel, children }: any) {
  return (
    <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <span className="text-sm font-bold text-white">{label}</span>
      </div>
      <div className="text-xs font-mono text-slate-400 mb-3 pl-6">
        └ {subLabel}
      </div>
      {children}
    </div>
  );
}

function BankToggle({ label, active, onClick }: any) {
  return (
    <motion.button
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      className={cn(
        "w-full flex items-center justify-between p-3 rounded-2xl border transition-all cursor-pointer focus:ring-2 focus:ring-emerald-500/50 outline-none",
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
    </motion.button>
  );
}