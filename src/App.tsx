import React, { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import Dashboard from './components/Dashboard';
import AutoTradePanel from './components/AutoTradePanel';
import SettingsPanel from './components/SettingsPanel';
import ApiKeysPanel from './components/ApiKeysPanel';
import BlacklistPanel from './components/BlacklistPanel';
import { mockStats, mockOpportunities, mockLogs, mockGlobalSettings, mockUserSettings, mockBlacklist } from './data/mock';
import { GlobalSettings, UserSettings, AutoTradeConfig, ApiKeyConfig, BlacklistEntry, SystemStats, ArbitrageOpportunity, AutoTradeLog } from './types';
import { api } from './services/api';
import { auth, db, loginWithGoogle, logout } from './firebase';
import { onAuthStateChanged, User } from 'firebase/auth';
import { doc, onSnapshot, setDoc } from 'firebase/firestore';
import { CheckCircle2, AlertTriangle, Info } from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [isAdmin, setIsAdmin] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const [stats, setStats] = useState<SystemStats>(mockStats);
  const [opportunities, setOpportunities] = useState<ArbitrageOpportunity[]>(mockOpportunities);
  const [logs, setLogs] = useState<AutoTradeLog[]>(mockLogs);
  const [globalSettings, setGlobalSettings] = useState<GlobalSettings>(mockGlobalSettings);
  const [userSettings, setUserSettings] = useState<UserSettings>(mockUserSettings);
  const [blacklist, setBlacklist] = useState<BlacklistEntry[]>(mockBlacklist);
  const [isConnected, setIsConnected] = useState<boolean | null>(null);

  const [user, setUser] = useState<User | null>(null);
  
  // Toast Notification System
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);

  const showToast = (message: string, type: 'success' | 'error' | 'info' = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (currentUser) => {
      setUser(currentUser);
    });
    return () => unsubscribe();
  }, []);

  useEffect(() => {
    if (!user) return;
    const userRef = doc(db, 'users', user.uid);
    const unsubscribe = onSnapshot(userRef, (docSnap) => {
      if (docSnap.exists()) {
        const data = docSnap.data() as UserSettings;
        setUserSettings(data);
        if (data.isTelegramAdmin !== undefined) {
          setIsAdmin(data.isTelegramAdmin);
        }
      } else {
        setDoc(userRef, mockUserSettings);
      }
    });
    return () => unsubscribe();
  }, [user]);

  useEffect(() => {
    const loadData = async () => {
      const isApiConnected = await api.checkConnection();
      setIsConnected(isApiConnected);

      const [fetchedStats, fetchedOpps, fetchedLogs, fetchedGlobal, fetchedBlacklist] = await Promise.all([
        api.getStats(),
        api.getOpportunities(),
        api.getLogs(),
        api.getGlobalSettings(),
        api.getBlacklist()
      ]);

      setStats(fetchedStats);
      setOpportunities(fetchedOpps);
      setLogs(fetchedLogs);
      setGlobalSettings(fetchedGlobal);
      setBlacklist(fetchedBlacklist);
    };

    loadData();

    const interval = setInterval(async () => {
      const isApiConnected = await api.checkConnection();
      setIsConnected(isApiConnected);

      const fetchedStats = await api.getStats();
      const fetchedOpps = await api.getOpportunities();
      setStats(fetchedStats);
      setOpportunities(fetchedOpps);
    }, 5000);

    return () => clearInterval(interval);
  }, []);

  const handleGlobalChange = async (key: keyof GlobalSettings, value: any) => {
    const newSettings = { ...globalSettings, [key]: value };
    setGlobalSettings(newSettings);
    await api.updateGlobalSettings(newSettings);
  };

  const handleUserChange = async (key: keyof UserSettings, value: any) => {
    const newSettings = { ...userSettings, [key]: value };
    setUserSettings(newSettings);
    if (user) {
      await setDoc(doc(db, 'users', user.uid), newSettings, { merge: true });
    }
    if (newSettings.telegramUserId && newSettings.autoSyncTelegram) {
      try {
        await api.updateTelegramSettings(newSettings.telegramUserId, { [key]: value });
      } catch (e) {
        showToast('Failed to auto-sync with bot', 'error');
      }
    }
  };

  const handleAutoTradeChange = async (newConfig: AutoTradeConfig) => {
    const newSettings = { ...userSettings, autoTrade: newConfig };
    setUserSettings(newSettings);
    if (user) {
      await setDoc(doc(db, 'users', user.uid), newSettings, { merge: true });
    }
    if (newSettings.telegramUserId && newSettings.autoSyncTelegram) {
      try {
        await api.updateTelegramSettings(newSettings.telegramUserId, { autoTrade: newConfig });
      } catch (e) {
        showToast('Failed to auto-sync with bot', 'error');
      }
    }
  };

  const handleSaveKey = async (exchange: string, config: ApiKeyConfig) => {
    const newApiKeys = { ...(userSettings.apiKeys || {}), [exchange.toLowerCase()]: config };
    const newSettings = { ...userSettings, apiKeys: newApiKeys };
    setUserSettings(newSettings);
    if (user) {
      await setDoc(doc(db, 'users', user.uid), newSettings, { merge: true });
    }
  };

  const handleConnectTelegram = async (telegramId: string) => {
    try {
      const botData = await api.syncTelegram(telegramId);
      if (botData) {
        const newApiKeys = { ...(userSettings.apiKeys || {}) };
        // Import keys from bot
        botData.keys.forEach((k: string) => {
          if (!newApiKeys[k]) {
            newApiKeys[k] = { key: 'imported_from_bot', secret: 'imported_from_bot' };
          }
        });

        const newSettings = {
          ...userSettings,
          ...botData.settings,
          apiKeys: newApiKeys,
          telegramUserId: telegramId,
          isTelegramAdmin: botData.isAdmin,
          autoSyncTelegram: true // Enable auto-sync by default
        };

        setUserSettings(newSettings);
        setIsAdmin(botData.isAdmin);
        if (user) {
          await setDoc(doc(db, 'users', user.uid), newSettings, { merge: true });
        }
        showToast('Successfully connected to Telegram Bot!', 'success');
      }
    } catch (error) {
      showToast('Failed to connect to Telegram Bot', 'error');
    }
  };

  const handleSyncFromBot = async () => {
    if (!userSettings.telegramUserId) return;
    try {
      const botData = await api.syncTelegram(userSettings.telegramUserId);
      if (botData) {
        const newSettings = {
          ...userSettings,
          ...botData.settings,
          isTelegramAdmin: botData.isAdmin
        };
        setUserSettings(newSettings);
        setIsAdmin(botData.isAdmin);
        if (user) {
          await setDoc(doc(db, 'users', user.uid), newSettings, { merge: true });
        }
        showToast('Settings pulled from Telegram Bot successfully', 'success');
      }
    } catch (error) {
      showToast('Failed to pull settings from bot', 'error');
    }
  };

  const handleSyncToBot = async () => {
    if (!userSettings.telegramUserId) return;
    try {
      await api.updateTelegramSettings(userSettings.telegramUserId, userSettings);
      showToast('Settings pushed to Telegram Bot successfully', 'success');
    } catch (error) {
      showToast('Failed to push settings to bot', 'error');
    }
  };

  const handleDisconnectTelegram = async () => {
    const newSettings = { ...userSettings };
    delete newSettings.telegramUserId;
    delete newSettings.isTelegramAdmin;
    delete newSettings.autoSyncTelegram;
    setUserSettings(newSettings);
    setIsAdmin(false); // Default back to false
    if (user) {
      await setDoc(doc(db, 'users', user.uid), { telegramUserId: null, isTelegramAdmin: null, autoSyncTelegram: null }, { merge: true });
    }
    showToast('Disconnected from Telegram Bot', 'info');
  };

  const handleUnban = async (merchantId: string, exchange: string) => {
    const success = await api.removeFromBlacklist(merchantId, exchange);
    if (success || !isConnected) {
      setBlacklist(prev => prev.filter(entry => !(entry.merchantId === merchantId && entry.exchange === exchange)));
    }
  };

  const connectedExchanges = Object.keys(userSettings.apiKeys || {}).map(k => k.toLowerCase());

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200 font-sans selection:bg-emerald-500/30 overflow-hidden relative">
      {/* Toast Notification */}
      {toast && (
        <div className="absolute top-4 right-4 z-50 animate-in fade-in slide-in-from-top-4 duration-300">
          <div className={cn(
            "flex items-center gap-3 px-4 py-3 rounded-xl shadow-lg border",
            toast.type === 'success' ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400" :
            toast.type === 'error' ? "bg-red-500/10 border-red-500/20 text-red-400" :
            "bg-blue-500/10 border-blue-500/20 text-blue-400"
          )}>
            {toast.type === 'success' && <CheckCircle2 className="w-5 h-5" />}
            {toast.type === 'error' && <AlertTriangle className="w-5 h-5" />}
            {toast.type === 'info' && <Info className="w-5 h-5" />}
            <span className="text-sm font-bold">{toast.message}</span>
          </div>
        </div>
      )}

      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        isAdmin={isAdmin}
        onToggleAdmin={() => setIsAdmin(!isAdmin)}
        isOpen={isSidebarOpen}
        setIsOpen={setIsSidebarOpen}
        isConnected={isConnected}
        user={user}
        onLogin={loginWithGoogle}
        onLogout={logout}
      />

      <main className="flex-1 overflow-y-auto p-8 relative">
        <div className="max-w-6xl mx-auto">
          {!user ? (
            <div className="flex flex-col items-center justify-center h-full mt-32">
              <div className="w-20 h-20 bg-emerald-500/20 rounded-full flex items-center justify-center mb-6">
                <div className="w-10 h-10 bg-emerald-500 rounded-full animate-pulse"></div>
              </div>
              <h1 className="text-3xl font-bold text-white mb-4">Welcome to Arbix Quantum</h1>
              <p className="text-slate-400 mb-8 text-center max-w-md">
                Please sign in to access your personalized dashboard, configure API keys, and manage auto-trading settings.
              </p>
              <button
                onClick={loginWithGoogle}
                className="px-8 py-4 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-2xl transition-all shadow-lg shadow-emerald-500/20"
              >
                Sign in with Google
              </button>
            </div>
          ) : (
            <>
              {activeTab === 'dashboard' && (
                <Dashboard stats={stats} opportunities={opportunities} />
              )}

              {activeTab === 'autotrade' && userSettings.autoTrade && (
                <AutoTradePanel
                  config={userSettings.autoTrade}
                  onConfigChange={handleAutoTradeChange}
                  logs={logs}
                />
              )}

              {activeTab === 'settings' && (
                <SettingsPanel
                  globalSettings={globalSettings}
                  userSettings={userSettings}
                  onGlobalChange={handleGlobalChange}
                  onUserChange={handleUserChange}
                  isAdmin={isAdmin}
                  onConnectTelegram={handleConnectTelegram}
                  onDisconnectTelegram={handleDisconnectTelegram}
                  onSyncFromBot={handleSyncFromBot}
                  onSyncToBot={handleSyncToBot}
                />
              )}

              {activeTab === 'apikeys' && (
                <ApiKeysPanel
                  connectedExchanges={connectedExchanges}
                  onSaveKey={handleSaveKey}
                />
              )}

              {activeTab === 'blacklist' && (
                <BlacklistPanel
                  blacklist={blacklist}
                  onUnban={handleUnban}
                />
              )}
            </>
          )}
        </div>
      </main>
    </div>
  );
}
