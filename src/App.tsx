import React, { useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';

import { useAppStore } from './store';
import { authApi, getSessionToken, setSessionToken } from './services/api';
import { applyAccent, resolveHue } from './lib/theme';
import LoadingVeil from './components/LoadingVeil';

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
/**
 * Заглушка на час підвантаження роутового чанка.
 *
 * Скелет зі смуг тут не підходив: він імітує розкладку сторінки, якої
 * ще немає, тож на кожному переході блимала чужа геометрія. Простий
 * знак, що думає, чесніший — і з'являється лише коли чекати справді
 * доводиться.
 */
function PageFallback() {
  return (
    <div className="min-h-screen bg-slate-950">
      <LoadingVeil />
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
