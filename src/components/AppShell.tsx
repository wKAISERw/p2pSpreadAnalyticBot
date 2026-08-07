import React, { useEffect, useState, lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom';
import { ErrorBoundary } from 'react-error-boundary';
import { Menu, X, Activity, AlertTriangle } from 'lucide-react';

import Sidebar from './Sidebar';
import Dashboard from './Dashboard';
import { CommandPalette } from './CommandPalette';
import ApiAccessBanner from './ApiAccessBanner';
import { useAppStore } from '../store';
import { api, checkConnection } from '../services/api';
import { signOutGoogle } from '../lib/google';
import { useCloudPrefs } from '../hooks/useCloudPrefs';
import { useSpreadAlerts } from '../hooks/useSpreadAlerts';
import { cn } from '../lib/utils';

// Розділи вантажаться на вимогу — інакше recharts і всі панелі їхали б
// користувачу ще до того, як він побачив перший спред.
const AutoTradePanel = lazy(() => import('./AutoTradePanel'));
const SettingsPanel = lazy(() => import('./SettingsPanel'));
const ApiKeysPanel = lazy(() => import('./ApiKeysPanel'));
const BlacklistPanel = lazy(() => import('./BlacklistPanel'));
const AnalyticsPanel = lazy(() =>
  import('./AnalyticsPanel').then(m => ({ default: m.AnalyticsPanel }))
);
const AccountsPanel = lazy(() => import('./AccountsPanel'));
const FiltersPanel = lazy(() => import('./FiltersPanel'));
const CardsPanel = lazy(() => import('./CardsPanel'));
const MonitoringPanel = lazy(() => import('./MonitoringPanel'));
const AdminUsersPanel = lazy(() => import('./AdminUsersPanel'));

function RouteFallback() {
  return (
    <div className="space-y-4 animate-pulse" aria-busy="true">
      <div className="h-8 w-48 bg-slate-900 rounded-lg" />
      <div className="h-32 bg-slate-900/70 rounded-3xl" />
      <div className="h-32 bg-slate-900/70 rounded-3xl" />
    </div>
  );
}

function ErrorFallback({ error, resetErrorBoundary }: { error: Error; resetErrorBoundary: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[400px] bg-slate-900/50 rounded-2xl border border-red-500/20 p-8 text-center">
      <div className="w-16 h-16 bg-red-500/10 rounded-full flex items-center justify-center mb-6">
        <AlertTriangle className="w-8 h-8 text-red-500" />
      </div>
      <h2 className="text-2xl font-bold text-white mb-2">Щось пішло не так</h2>
      <p className="text-slate-400 mb-6 max-w-md">
        Розділ впав із помилкою. Решта дашборду працює — можна спробувати ще раз.
      </p>
      <pre className="bg-slate-950 p-4 rounded-lg text-red-400 text-sm mb-6 max-w-2xl overflow-auto text-left w-full border border-slate-800">
        {error.message}
      </pre>
      <button
        onClick={resetErrorBoundary}
        className="px-6 py-3 bg-slate-800 hover:bg-slate-700 text-white font-medium rounded-xl transition-colors"
      >
        Спробувати ще раз
      </button>
    </div>
  );
}

/**
 * Робоча частина: сайдбар, шапка й розділи дашборду.
 *
 * Винесена з App, коли з'явився публічний сайт: лендингу не потрібні ані
 * polling стану, ані звукові алерти, ані перевірка сесії — усе це тепер
 * живе тут і стартує лише після входу.
 */
export default function AppShell() {
  const navigate = useNavigate();
  const { isFocusMode, connection, setConnection, setGlobalSettings } = useAppStore();
  const auth = useAppStore(state => state.auth);
  const setAuth = useAppStore(state => state.setAuth);

  // На телефоні сайдбар — оверлей на всю ширину. Відкритий за
  // замовчуванням він означав, що застосунок стартує з меню поверх контенту.
  const [isSidebarOpen, setIsSidebarOpen] = useState(
    () => typeof window === 'undefined' || window.innerWidth >= 768
  );
  // Відновлення сесії робить App — воно потрібне на всіх сторінках,
  // а не лише всередині дашборду.
  const authRestored = useAppStore(state => state.authRestored);

  // Прив'язаний Google дає налаштуванням сайту переїжджати між браузерами.
  const hasGoogleLink = Boolean(auth?.identities?.some(i => i.provider === 'google'));
  useCloudPrefs(hasGoogleLink);

  // Звук про новий спред живе тут: у Dashboard він замовкав на будь-якій
  // іншій сторінці разом із розмонтованим компонентом.
  useSpreadAlerts(Boolean(auth) && connection === 'live');

  // Вихід гасить обидві сесії: нашу і Google, якщо він був задіяний.
  const handleLogout = async () => {
    setAuth(null);
    try {
      await signOutGoogle();
    } catch {
      /* Google-сесії могло й не бути */
    }
    navigate('/');
  };

  useEffect(() => {
    const checkConn = async () => setConnection(await checkConnection());
    checkConn();
    const interval = setInterval(checkConn, 10000);
    return () => clearInterval(interval);
  }, [setConnection]);

  // Без цього адмінка редагувала б значення «з голови» і першою ж зміною
  // затирала реальний конфіг сканера.
  useEffect(() => {
    if (connection !== 'live') return;
    api.getGlobalSettings().then(setGlobalSettings).catch(() => {});
  }, [connection, setGlobalSettings]);

  if (!authRestored) {
    return (
      <div className="flex items-center justify-center h-screen bg-slate-950 text-slate-500 text-sm">
        Відновлюю сесію…
      </div>
    );
  }

  if (!auth) return <Navigate to="/login" replace />;

  return (
        <div className="page-enter flex h-screen bg-slate-950 text-slate-200 font-sans selection:bg-accent-500/30 overflow-hidden relative">
      {!isFocusMode && (
        <div className="md:hidden absolute top-0 left-0 right-0 h-16 bg-slate-900 border-b border-slate-800 flex items-center px-4 z-30 gap-4">
          <button
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className="p-2 bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
          >
            {isSidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-accent-500 rounded-xl flex items-center justify-center shadow-lg shadow-accent-500/20">
              <Activity className="text-slate-950 w-5 h-5" />
            </div>
            <span className="font-bold text-white tracking-tight text-lg">ARBIX</span>
          </div>
        </div>
      )}

      {!isFocusMode && (
        <Sidebar isOpen={isSidebarOpen} setIsOpen={setIsSidebarOpen} onLogout={handleLogout} />
      )}

      <main
        className={cn(
          'flex-1 overflow-y-auto relative',
          isFocusMode ? 'p-4' : 'p-4 md:p-8 pt-20 md:pt-8'
        )}
      >
        <CommandPalette />
        <div className={cn('mx-auto', isFocusMode ? 'max-w-full' : 'max-w-6xl')}>
          <ErrorBoundary FallbackComponent={ErrorFallback} onReset={() => navigate('/app')}>
            <ApiAccessBanner />
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route index element={<Dashboard />} />
                <Route path="autotrade" element={<AutoTradePanel />} />
                <Route path="analytics" element={<AnalyticsPanel />} />
                <Route path="settings" element={<SettingsPanel />} />
                <Route path="apikeys" element={<ApiKeysPanel />} />
                <Route path="accounts" element={<AccountsPanel />} />
                <Route path="filters" element={<FiltersPanel />} />
                <Route path="cards" element={<CardsPanel />} />
                <Route path="monitoring" element={<MonitoringPanel />} />
                <Route path="users" element={<AdminUsersPanel />} />
                <Route path="blacklist" element={<BlacklistPanel />} />
                <Route path="*" element={<Navigate to="/app" replace />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
