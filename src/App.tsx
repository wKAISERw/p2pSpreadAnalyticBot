import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { ErrorBoundary } from 'react-error-boundary';
import Sidebar from './components/Sidebar';
import Dashboard from './components/Dashboard';
import AutoTradePanel from './components/AutoTradePanel';
import SettingsPanel from './components/SettingsPanel';
import ApiKeysPanel from './components/ApiKeysPanel';
import BlacklistPanel from './components/BlacklistPanel';
import { AnalyticsPanel } from './components/AnalyticsPanel';
import { CommandPalette } from './components/CommandPalette';
import { useAppStore } from './store';
import { api } from './services/api';
import { auth, db, loginWithGoogle, logout } from './firebase';
import { onAuthStateChanged, User } from 'firebase/auth';
import { doc, onSnapshot, setDoc } from 'firebase/firestore';
import { Menu, X, Activity, AlertTriangle } from 'lucide-react';
import { cn } from './lib/utils';
import { Toaster } from 'sonner';
import { UserSettings } from './types';
import { mockUserSettings } from './data/mock';

function ErrorFallback({ error, resetErrorBoundary }: { error: Error, resetErrorBoundary: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[400px] bg-slate-900/50 rounded-2xl border border-red-500/20 p-8 text-center">
      <div className="w-16 h-16 bg-red-500/10 rounded-full flex items-center justify-center mb-6">
        <AlertTriangle className="w-8 h-8 text-red-500" />
      </div>
      <h2 className="text-2xl font-bold text-white mb-2">Something went wrong</h2>
      <p className="text-slate-400 mb-6 max-w-md">
        An unexpected error occurred in the application. Our team has been notified.
      </p>
      <pre className="bg-slate-950 p-4 rounded-lg text-red-400 text-sm mb-6 max-w-2xl overflow-auto text-left w-full border border-slate-800">
        {error.message}
      </pre>
      <button
        onClick={resetErrorBoundary}
        className="px-6 py-3 bg-slate-800 hover:bg-slate-700 text-white font-medium rounded-xl transition-colors"
      >
        Try again
      </button>
    </div>
  );
}

export default function App() {
  const { isFocusMode, setUserSettings, setIsAdmin, isConnected, setIsConnected } = useAppStore();
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [user, setUser] = useState<User | null>(null);

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
  }, [user, setUserSettings, setIsAdmin]);

  useEffect(() => {
    const checkConn = async () => {
      const isApiConnected = await api.checkConnection();
      setIsConnected(isApiConnected);
    };
    checkConn();
    const interval = setInterval(checkConn, 10000);
    return () => clearInterval(interval);
  }, [setIsConnected]);

  return (
    <Router>
      <div className="flex h-screen bg-slate-950 text-slate-200 font-sans selection:bg-emerald-500/30 overflow-hidden relative">
        <Toaster theme="dark" position="top-right" richColors />

        {/* Mobile Header */}
        {!isFocusMode && (
          <div className="md:hidden absolute top-0 left-0 right-0 h-16 bg-slate-900 border-b border-slate-800 flex items-center px-4 z-30 gap-4">
            <button 
              onClick={() => setIsSidebarOpen(!isSidebarOpen)} 
              className="p-2 bg-slate-800 rounded-lg text-slate-400 hover:text-white transition-colors"
            >
              {isSidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 bg-emerald-500 rounded-xl flex items-center justify-center shadow-lg shadow-emerald-500/20">
                <Activity className="text-slate-950 w-5 h-5" />
              </div>
              <span className="font-bold text-white tracking-tight text-lg">ARBIX</span>
            </div>
          </div>
        )}

        {!isFocusMode && (
          <Sidebar
            isOpen={isSidebarOpen}
            setIsOpen={setIsSidebarOpen}
            user={user}
            onLogin={loginWithGoogle}
            onLogout={logout}
          />
        )}

        <main className={cn("flex-1 overflow-y-auto relative", isFocusMode ? "p-4" : "p-4 md:p-8 pt-20 md:pt-8")}>
          <CommandPalette />
          <div className={cn("mx-auto", isFocusMode ? "max-w-full" : "max-w-6xl")}>
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
                  className="px-8 py-4 bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold rounded-2xl transition-all shadow-lg shadow-emerald-500/20 focus:ring-2 focus:ring-emerald-500/50 focus:border-transparent outline-none"
                >
                  Sign in with Google
                </button>
              </div>
            ) : (
              <ErrorBoundary FallbackComponent={ErrorFallback} onReset={() => window.location.href = '/'}>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/autotrade" element={<AutoTradePanel />} />
                  <Route path="/analytics" element={<AnalyticsPanel />} />
                  <Route path="/settings" element={<SettingsPanel />} />
                  <Route path="/apikeys" element={<ApiKeysPanel />} />
                  <Route path="/blacklist" element={<BlacklistPanel />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </ErrorBoundary>
            )}
          </div>
        </main>
      </div>
    </Router>
  );
}
