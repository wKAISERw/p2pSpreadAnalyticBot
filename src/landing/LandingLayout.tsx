import React, { useEffect, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { Activity, Menu, X, ArrowUpRight, User } from 'lucide-react';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';
import AuroraField from './AuroraField';
import { useReveal } from './motion';

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
  const auth = useAppStore(state => state.auth);
  const isLoggedIn = Boolean(auth);

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

      <header className="header-condense sticky top-0 z-40 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur-2xl">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2.5 shrink-0">
            <span className="w-9 h-9 rounded-xl bg-accent-500 flex items-center justify-center shadow-lg shadow-accent-500/25">
              <Activity className="w-5 h-5 text-slate-950" />
            </span>
            <span className="font-bold tracking-tight text-white text-base sm:text-lg">
              ARBIX <span className="text-accent-400">QUANTUM</span>
            </span>
          </Link>

          <nav className="hidden md:flex items-center gap-2">
            {NAV.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'px-4 py-2 rounded-xl text-sm font-semibold transition-all',
                    isActive
                      ? 'text-accent-400 bg-accent-500/10 border border-accent-500/20'
                      : 'text-slate-300 hover:text-white hover:bg-slate-900/60'
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-3">
            <Link
              to={isLoggedIn ? '/app' : '/login'}
              className="hidden sm:inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-accent-500 hover:bg-accent-400 text-slate-950 text-sm font-bold transition-all shadow-md shadow-accent-500/20"
            >
              {isLoggedIn ? 'До дашборду' : 'Увійти'}
              <ArrowUpRight className="w-4 h-4" />
            </Link>

            {/*
              Профіль поруч із кнопкою: показує, під ким ти зайшов, і веде
              одразу в налаштування акаунта. Для адміна — окрема позначка,
              бо переплутати робочий і адмінський вхід дорого.
            */}
            {isLoggedIn && (
              <Link
                to="/app/settings?tab=account"
                title={`Telegram ID ${auth?.telegramId}${auth?.isAdmin ? ' · адміністратор' : ''}`}
                className="hidden sm:flex items-center justify-center w-10 h-10 rounded-xl bg-slate-900 border border-slate-800 text-slate-300 hover:text-white hover:border-slate-700 transition-colors relative"
              >
                <User className="w-4.5 h-4.5" />
                {auth?.isAdmin && (
                  <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-orange-500 border-2 border-slate-950" />
                )}
              </Link>
            )}

            <button
              onClick={() => setIsMenuOpen(!isMenuOpen)}
              className="md:hidden p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-accent-400 hover:text-accent-300 active:scale-95 transition-all shadow-md flex items-center justify-center min-w-[42px] min-h-[42px] shrink-0"
              aria-label="Меню"
            >
              {isMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>

        {/*
          Смужка прогресу читання. Ширину рахує браузер зі scroll-таймлайну
          — жодного обробника scroll у JS.
        */}
        <div
          className="scroll-progress absolute bottom-0 left-0 h-px w-full bg-accent-500"
          aria-hidden
        />
      </header>

      {/*
        Оверлей меню живе ПОЗА <header> — і це не косметика.

        У хедера backdrop-filter, а будь-який backdrop-filter, filter,
        transform чи contain на предку створює containing block для
        position: fixed. Тобто inset-0 всередині хедера рахувався від
        його власної коробки у 65px, а не від екрана: меню відкривалось
        смужкою у 48 пікселів і виглядало як «не працює».
      */}
      {isMenuOpen && (
        <div className="md:hidden fixed inset-0 z-50 flex flex-col animate-rise">
          {/*
            Розмиття фону з м'яким згасанням. Маска робить шар щільним
            там, де лежать пункти меню, і поступово прозорим донизу —
            інакше різкий край виглядав би як приклеєний прямокутник.
          */}
          <div className="menu-scrim absolute inset-0" onClick={() => setIsMenuOpen(false)} />

          <div className="relative z-10 flex items-center justify-between h-16 px-4 shrink-0">
            <Link
              to="/"
              onClick={() => setIsMenuOpen(false)}
              className="flex items-center gap-2.5"
            >
              <span className="w-9 h-9 rounded-xl bg-accent-500 flex items-center justify-center">
                <Activity className="w-5 h-5 text-slate-950" />
              </span>
              <span className="font-bold tracking-tight text-white">
                ARBIX <span className="text-accent-400">QUANTUM</span>
              </span>
            </Link>

            <button
              onClick={() => setIsMenuOpen(false)}
              className="p-2.5 rounded-xl bg-slate-900 border border-slate-700 text-accent-400 min-w-[42px] min-h-[42px] flex items-center justify-center"
              aria-label="Закрити меню"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <nav className="relative z-10 px-5 pt-4 pb-10 space-y-3 overflow-y-auto">
            {NAV.map((item, i) => (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={() => setIsMenuOpen(false)}
                style={{ animationDelay: `${i * 55}ms` }}
                className={({ isActive }) =>
                  cn(
                    'flex items-center justify-between px-6 py-4 rounded-2xl text-lg font-bold border transition-all animate-rise',
                    isActive
                      ? 'bg-accent-500/15 border-accent-500/40 text-accent-400'
                      : 'bg-slate-900 border-slate-800 text-slate-100 active:bg-slate-800'
                  )
                }
              >
                {item.label}
                <ArrowUpRight className="w-5 h-5 opacity-60 text-accent-400" />
              </NavLink>
            ))}

            <Link
              to={isLoggedIn ? '/app' : '/login'}
              onClick={() => setIsMenuOpen(false)}
              className="flex items-center justify-center gap-2.5 px-6 py-4 mt-5 rounded-2xl bg-accent-500 text-slate-950 text-lg font-bold shadow-xl shadow-accent-500/20 animate-rise"
              style={{ animationDelay: `${NAV.length * 55}ms` }}
            >
              {isLoggedIn ? 'До дашборду' : 'Увійти'}
              <ArrowUpRight className="w-5 h-5" />
            </Link>
          </nav>
        </div>
      )}


      <main className="relative z-10">{children}</main>

      <footer className="relative z-10 border-t border-slate-800/60 mt-24">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 py-12">
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

/**
 * Спільна обгортка секції — щоб відступи й ширина не розповзались.
 *
 * На дуже широких екранах контейнер розтягується до 1440px. З 1280px на
 * моніторі 2125px (а це звичайні 1920 при масштабі 90%) з боків лишалось
 * по 423px порожнечі — сторінка виглядала вузькою смужкою посередині.
 */
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
    <section
      id={id}
      className={cn('mx-auto max-w-7xl 2xl:max-w-[90rem] px-4 sm:px-6 py-16 sm:py-24', className)}
    >
      {children}
    </section>
  );
}

/**
 * Наскрізна лінія між секціями.
 *
 * До неї сторінка була стрічкою блоків, що просто йшли один за одним:
 * межі вгадувались лише за відступами. Лінія на всю ширину дає ритм і
 * відчуття специфікації, а не рекламної сторінки.
 *
 * Промальовується від центру, коли до неї доскролили — інакше це просто
 * ще одна статична смуга.
 */
export function Rule() {
  const { ref, shown } = useReveal<HTMLDivElement>(true);

  return (
    <div ref={ref} className="relative h-px w-full bg-slate-800/40" aria-hidden>
      <span
        className={cn(
          'absolute inset-0 origin-center transition-transform duration-1000 ease-out',
          shown ? 'scale-x-100' : 'scale-x-0'
        )}
        style={{
          background:
            'linear-gradient(90deg, transparent, rgb(var(--accent-rgb) / 0.35) 30%, rgb(var(--accent-rgb) / 0.35) 70%, transparent)',
        }}
      />
    </div>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  num,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  /** Порядковий номер секції — «/03». Дає сторінці відчуття документа. */
  num?: string;
}) {
  return (
    <div className="max-w-2xl mb-12">
      {eyebrow && (
        <div className="flex items-baseline gap-2.5 mb-3">
          {num && (
            <span className="tag-mono text-xs font-bold text-slate-600 tabular-nums">/{num}</span>
          )}
          <span className="text-xs font-bold uppercase tracking-[0.2em] text-accent-400">
            {eyebrow}
          </span>
        </div>
      )}
      <h2 className="text-3xl sm:text-4xl font-bold text-white tracking-tight mb-4">{title}</h2>
      {description && <p className="text-slate-400 leading-relaxed">{description}</p>}
    </div>
  );
}
