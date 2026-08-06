import React, { useEffect, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { Activity, Menu, X, ArrowUpRight } from 'lucide-react';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';
import AuroraField from './AuroraField';

export const NAV = [
  { to: '/features', label: 'Можливості' },
  { to: '/how-it-works', label: 'Як це працює' },
  { to: '/security', label: 'Безпека' },
];

/**
 * Каркас публічних сторінок.
 *
 * Свідомо не використовує ані motion, ані клієнт API: лендинг має
 * відкриватись швидко на телефоні з поганим зв'язком, а весь рух тут
 * робиться на CSS-анімаціях, які не тримають головний потік.
 */
export default function LandingLayout({ children }: { children: React.ReactNode }) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const { pathname } = useLocation();
  const isLoggedIn = Boolean(useAppStore(state => state.auth));

  // Перехід між сторінками не має лишати відкрите мобільне меню
  // і зберігати позицію прокрутки попередньої сторінки.
  useEffect(() => {
    setIsMenuOpen(false);
    window.scrollTo(0, 0);
  }, [pathname]);

  // Оверлей на весь екран: сторінка під ним не має прокручуватись.
  useEffect(() => {
    document.body.style.overflow = isMenuOpen ? 'hidden' : '';
    return () => {
      document.body.style.overflow = '';
    };
  }, [isMenuOpen]);

  return (
    <div className="relative min-h-screen bg-slate-950 text-slate-200 font-sans selection:bg-accent-500/30">
      {/* Фон живе під усім вмістом і не бере участі в потоці */}
      <AuroraField />

      <header className="header-condense sticky top-0 z-40 border-b border-slate-800/60 bg-slate-950/80 backdrop-blur-xl">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2.5 shrink-0">
            <span className="w-9 h-9 rounded-xl bg-accent-500 flex items-center justify-center shadow-lg shadow-accent-500/25">
              <Activity className="w-5 h-5 text-slate-950" />
            </span>
            <span className="font-bold tracking-tight text-white">
              ARBIX <span className="text-accent-400">QUANTUM</span>
            </span>
          </Link>

          <nav className="hidden md:flex items-center gap-1">
            {NAV.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'px-3 py-2 rounded-lg text-sm font-medium transition-colors',
                    isActive ? 'text-accent-400' : 'text-slate-400 hover:text-white'
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <Link
              to={isLoggedIn ? '/app' : '/login'}
              className="hidden sm:inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-accent-500 hover:bg-accent-400 text-slate-950 text-sm font-bold transition-colors"
            >
              {isLoggedIn ? 'До дашборду' : 'Увійти'}
              <ArrowUpRight className="w-4 h-4" />
            </Link>

            <button
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              className="md:hidden p-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-400"
              aria-label="Меню"
            >
              {isMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>

        {/*
          Повноекранний оверлей замість випадайчика: у нього легше влучити
          пальцем, і він не притискає контент шапкою.
        */}
        {isMenuOpen && (
          <div className="md:hidden fixed inset-0 top-16 z-40 bg-slate-950/95 backdrop-blur-xl animate-rise">
            <nav className="px-4 py-6 space-y-2">
              {NAV.map((item, i) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  style={{ animationDelay: `${i * 50}ms` }}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center justify-between px-5 py-4 rounded-2xl text-lg font-bold border animate-rise',
                      isActive
                        ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                        : 'bg-slate-900/60 border-slate-800 text-slate-200'
                    )
                  }
                >
                  {item.label}
                  <ArrowUpRight className="w-5 h-5 opacity-40" />
                </NavLink>
              ))}

              <Link
                to={isLoggedIn ? '/app' : '/login'}
                className="flex items-center justify-center gap-2 px-5 py-4 rounded-2xl bg-accent-500 text-slate-950 text-lg font-bold mt-4 animate-rise"
                style={{ animationDelay: `${NAV.length * 50}ms` }}
              >
                {isLoggedIn ? 'До дашборду' : 'Увійти'}
                <ArrowUpRight className="w-5 h-5" />
              </Link>
            </nav>
          </div>
        )}

        {/*
          Смужка прогресу читання. Ширина рахується самим браузером зі
          scroll-таймлайну — жодного обробника scroll у JS.
        */}
        <div
          className="scroll-progress absolute bottom-0 left-0 h-px w-full bg-accent-500"
          aria-hidden
        />
      </header>

      <main className="relative z-10">{children}</main>

      <footer className="relative z-10 border-t border-slate-800/60 mt-24">
        <div className="mx-auto max-w-6xl px-4 sm:px-6 py-12">
          <div className="flex flex-col md:flex-row md:items-start justify-between gap-8">
            <div className="max-w-sm">
              <div className="flex items-center gap-2.5 mb-3">
                <span className="w-8 h-8 rounded-lg bg-accent-500 flex items-center justify-center">
                  <Activity className="w-4 h-4 text-slate-950" />
                </span>
                <span className="font-bold text-white">ARBIX QUANTUM</span>
              </div>
              <p className="text-sm text-slate-400 leading-relaxed">
                Сканер P2P-спредів із антифрод-аналізом контрагентів.
                Знаходить різницю курсів між біржами й перевіряє, з ким ти
                збираєшся торгувати.
              </p>
            </div>

            <nav className="flex flex-col gap-2">
              {NAV.map(item => (
                <Link key={item.to} to={item.to} className="text-sm text-slate-400 hover:text-white transition-colors">
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>

          {/*
            Це не інвестиційна порада і не обіцянка доходу — інструмент
            показує ринкові дані й оцінює ризик контрагента. Рішення про
            угоду приймає людина, і гроші ризикує теж вона.
          */}
          <p className="mt-10 pt-6 border-t border-slate-800/60 text-xs text-slate-500 leading-relaxed">
            Arbix Quantum — інструмент аналізу ринку, а не інвестиційна порада.
            Спред, який показує сканер, не гарантує прибутку: ціни й доступні
            обсяги змінюються, а частина ризику лежить на стороні контрагента
            й банку. Рішення про кожну угоду приймаєш ти.
          </p>
        </div>
      </footer>
    </div>
  );
}

/** Спільна обгортка секції — щоб відступи й ширина не розповзались. */
export function Section({
  children,
  className,
  id,
}: {
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={cn('mx-auto max-w-6xl px-4 sm:px-6 py-16 sm:py-24', className)}>
      {children}
    </section>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
}) {
  return (
    <div className="max-w-2xl mb-12">
      {eyebrow && (
        <div className="text-xs font-bold uppercase tracking-[0.2em] text-accent-400 mb-3">
          {eyebrow}
        </div>
      )}
      <h2 className="text-3xl sm:text-4xl font-bold text-white tracking-tight mb-4">{title}</h2>
      {description && <p className="text-slate-400 leading-relaxed">{description}</p>}
    </div>
  );
}
