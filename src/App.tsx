import React, { useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';

import { useAppStore } from './store';
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

function PageFallback() {
  return <div className="min-h-screen bg-slate-950" aria-busy="true" />;
}

export default function App() {
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
