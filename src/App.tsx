import React, { useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';

import { useAppStore } from './store';
import { authApi, getSessionToken, setSessionToken } from './services/api';
import { applyAccent, resolveHue } from './lib/theme';

// Публічний сайт — окремі чанки. Людині, що прийшла на лендинг, не
// потрібні ані панелі дашборду, ані клієнт API.
const LandingPage = lazy(() => import('./landing/LandingPage'));
const FeaturesPage = lazy(() => import('./landing/FeaturesPage'));
const HowItWorksPage = lazy(() => import('./landing/HowItWorksPage'));
const SecurityPage = lazy(() => import('./landing/SecurityPage'));
const LandingLayout = lazy(() => import('./landing/LandingLayout'));

// Дашборд і екран входу теж ліниві: відвідувачу лендингу не потрібні ані
// панелі, ані клієнт API, ані SWR. Інакше публічна сторінка тягнула б
// увесь застосунок разом із собою.
const AppShell = lazy(() => import('./components/AppShell'));
const LoginScreen = lazy(() => import('./components/LoginScreen'));

/**
 * Заглушка на час підвантаження чанка сторінки.
 *
 * Раніше це був порожній прямокутник — на повільному звʼязку виглядало
 * як «сайт помер». Тепер видно каркас майбутньої сторінки: шапка, місце
 * під заголовок, сітка блоків. Людина розуміє, що щось вантажиться, а не
 * зламалось.
 */
function PageFallback() {
  return (
    <div className="min-h-screen bg-slate-950" aria-busy="true" aria-label="Завантаження">
      <div className="h-16 border-b border-slate-800/60 flex items-center px-4 sm:px-6 gap-3">
        <div className="w-9 h-9 rounded-xl bg-slate-800 animate-pulse" />
        <div className="h-4 w-36 rounded bg-slate-800 animate-pulse" />
      </div>

      <div className="mx-auto max-w-6xl px-4 sm:px-6 py-16 space-y-8">
        <div className="space-y-4">
          <div className="h-3 w-28 rounded bg-slate-800/80 animate-pulse" />
          <div className="h-10 w-2/3 max-w-lg rounded-lg bg-slate-800 animate-pulse" />
          <div className="h-4 w-full max-w-xl rounded bg-slate-900 animate-pulse" />
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[0, 1, 2, 3, 4, 5].map(i => (
            <div
              key={i}
              className="h-36 rounded-2xl bg-slate-900/70 border border-slate-800/60 animate-pulse"
              style={{ animationDelay: `${i * 80}ms` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const setAuth = useAppStore(state => state.setAuth);
  const setAuthRestored = useAppStore(state => state.setAuthRestored);

  /*
   * Відновлення сесії живе тут, а не в AppShell.
   *
   * Раніше воно спрацьовувало тільки на /app/*, тож людина, яка
   * поверталась на головну, бачила в хедері «Увійти» — токен у
   * localStorage лежав, але ніхто його не перевіряв. Виглядало це рівно
   * як «сесія не зберігається», хоча сесія була жива.
   *
   * Токен усе одно перевіряємо на бекенді: він міг протухнути або бути
   * підписаним іншим ботом, і сама його наявність нічого не гарантує.
   */
  useEffect(() => {
    const token = getSessionToken();
    if (!token) {
      setAuthRestored(true);
      return;
    }

    authApi
      .me()
      .then(me =>
        setAuth({ token, telegramId: me.telegramId, isAdmin: me.isAdmin, identities: me.identities })
      )
      .catch(() => setSessionToken(''))
      .finally(() => setAuthRestored(true));
  }, [setAuth, setAuthRestored]);

  // Гама застосовується до першого кадру і при кожній зміні — зокрема
  // коли налаштування приїхали з іншого пристрою через хмару.
  const accentColor = useAppStore(state => state.userSettings.accentColor);
  useEffect(() => {
    applyAccent(resolveHue(accentColor), false);
  }, [accentColor]);

  return (
    <Router>
      <Toaster theme="dark" position="top-right" richColors />
      <Suspense fallback={<PageFallback />}>
        <Routes>
          {/* Публічна частина */}
          <Route path="/" element={<LandingPage />} />
          <Route path="/features" element={<FeaturesPage />} />
          <Route path="/how-it-works" element={<HowItWorksPage />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/login" element={<LandingLayout><LoginScreen /></LandingLayout>} />

          {/* Дашборд */}
          <Route path="/app/*" element={<AppShell />} />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Router>
  );
}
