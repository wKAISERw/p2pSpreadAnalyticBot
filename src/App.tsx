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

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [isAdmin, setIsAdmin] = useState(true); // Toggle for demo purposes
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  // State for API data
 const [stats, setStats] = useState<SystemStats>(mockStats);
  const [opportunities, setOpportunities] = useState<ArbitrageOpportunity[]>(mockOpportunities);
  const [logs, setLogs] = useState<AutoTradeLog[]>(mockLogs);
  const [globalSettings, setGlobalSettings] = useState<GlobalSettings>(mockGlobalSettings);
  const [userSettings, setUserSettings] = useState<UserSettings>(mockUserSettings);
  const [connectedExchanges, setConnectedExchanges] = useState<string[]>(['binance', 'bybit']);
  const [blacklist, setBlacklist] = useState<BlacklistEntry[]>(mockBlacklist);
  const [isConnected, setIsConnected] = useState<boolean | null>(null);

  // Fetch initial data
  useEffect(() => {
    const loadData = async () => {
      const isApiConnected = await api.checkConnection();
      setIsConnected(isApiConnected);

      const [fetchedStats, fetchedOpps, fetchedLogs, fetchedGlobal, fetchedUser, fetchedBlacklist] = await Promise.all([
        api.getStats(),
        api.getOpportunities(),
        api.getLogs(),
        api.getGlobalSettings(),
        api.getUserSettings(),
        api.getBlacklist()
      ]);

      setStats(fetchedStats);
      setOpportunities(fetchedOpps);
      setLogs(fetchedLogs);
      setGlobalSettings(fetchedGlobal);
      setUserSettings(fetchedUser);
      setBlacklist(fetchedBlacklist);
    };

    loadData();

    // Poll for live updates (stats and opportunities) every 5 seconds
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

  const handleUserChange = (key: keyof UserSettings, value: any) => {
    setUserSettings(prev => ({ ...prev, [key]: value }));
  };

  const handleAutoTradeChange = (newConfig: AutoTradeConfig) => {
    setUserSettings(prev => ({ ...prev, autoTrade: newConfig }));
  };

  const handleSaveKey = async (exchange: string, config: ApiKeyConfig) => {
    // Simulate API call
    await new Promise(resolve => setTimeout(resolve, 1000));
    setConnectedExchanges(prev => [...new Set([...prev, exchange.toLowerCase()])]);
  };

  const handleUnban = (merchantId: string, exchange: string) => {
    setBlacklist(prev => prev.filter(entry => !(entry.merchantId === merchantId && entry.exchange === exchange)));
  };

  return (
    <div className="flex h-screen bg-slate-950 text-slate-200 font-sans selection:bg-emerald-500/30 overflow-hidden">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        isAdmin={isAdmin}
        onToggleAdmin={() => setIsAdmin(!isAdmin)}
        isOpen={isSidebarOpen}
        setIsOpen={setIsSidebarOpen}
        isConnected={isConnected}
      />

      <main className="flex-1 overflow-y-auto p-8 relative">
        <div className="max-w-6xl mx-auto">
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
        </div>
      </main>
    </div>
  );
}


