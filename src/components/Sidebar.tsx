import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Activity, Key, Settings, ShieldBan, LogOut, ChevronLeft, ChevronRight, BarChart3, Wallet, SlidersHorizontal, CreditCard, MonitorDot, Users, Home } from 'lucide-react';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';

interface SidebarProps {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
  /** Гасить і сесію дашборду, і Google-сесію, якщо вона є. */
  onLogout: () => void;
}

export default function Sidebar({ isOpen, setIsOpen, onLogout }: SidebarProps) {
  const connection = useAppStore(state => state.connection);
  const auth = useAppStore(state => state.auth);
  const setAuth = useAppStore(state => state.setAuth);

  // Раніше стан був бінарним і при обриві зв'язку підписувався як
  // "Mock Data" — хоча жодних моків уже немає. Розрізняємо 401 і offline:
  // це різні проблеми з різними діями.
  const connectionView = {
    live: { dot: 'bg-accent-500', box: 'bg-accent-500/10 border-accent-500/20 text-accent-400', label: 'Live Data', title: 'Бекенд підключено' },
    connecting: { dot: 'bg-slate-500 animate-pulse', box: 'bg-slate-800 border-slate-700 text-slate-400', label: 'Connecting...', title: 'Перевіряю зв\'язок' },
    unauthorized: { dot: 'bg-orange-500', box: 'bg-orange-500/10 border-orange-500/20 text-orange-400', label: 'No API Key', title: 'Бекенд відхилив ключ (401)' },
    offline: { dot: 'bg-red-500', box: 'bg-red-500/10 border-red-500/20 text-red-400', label: 'Offline', title: 'Бекенд недоступний' },
    error: { dot: 'bg-red-500', box: 'bg-red-500/10 border-red-500/20 text-red-400', label: 'API Error', title: 'Бекенд повернув помилку' },
  }[connection];

  /*
   * Розділи згруповані за питанням, з яким сюди приходять: «що зараз на
   * ринку», «як налаштований пошук», «де мої гроші», «чи все живе».
   *
   * Плоский список із одинадцяти пунктів читався як звалище: «Картки»
   * стояли між «Фільтрами» й «Моніторингом», а «Ключі бірж» — через три
   * пункти від «Акаунтів бірж», хоча це дві половини однієї справи.
   */
  const navGroups: {
    title: string;
    items: { id: string; path: string; label: string; icon: typeof LayoutDashboard }[];
  }[] = [
    {
      title: 'Огляд',
      items: [
        { id: 'dashboard', path: '/app', label: 'Дашборд', icon: LayoutDashboard },
        { id: 'analytics', path: '/app/analytics', label: 'Аналітика', icon: BarChart3 },
      ],
    },
    {
      title: 'Сканер',
      items: [
        { id: 'filters', path: '/app/filters', label: 'Фільтри', icon: SlidersHorizontal },
        { id: 'blacklist', path: '/app/blacklist', label: 'Чорний список', icon: ShieldBan },
      ],
    },
    {
      title: 'Гроші',
      items: [
        { id: 'cards', path: '/app/cards', label: 'Картки', icon: CreditCard },
        { id: 'accounts', path: '/app/accounts', label: 'Баланси бірж', icon: Wallet },
        { id: 'apikeys', path: '/app/apikeys', label: 'Ключі бірж', icon: Key },
      ],
    },
    {
      title: 'Система',
      items: [
        { id: 'monitoring', path: '/app/monitoring', label: 'Моніторинг', icon: MonitorDot },
        { id: 'logs', path: '/app/logs', label: 'Логи', icon: Activity },
        // Бекенд усе одно перевіряє права — тут лише не показуємо зайвого.
        ...(auth?.isAdmin
          ? [{ id: 'users', path: '/app/users', label: 'Користувачі', icon: Users }]
          : []),
        { id: 'settings', path: '/app/settings', label: 'Налаштування', icon: Settings },
      ],
    },
  ];

  return (
    <>
      {/* Mobile Backdrop */}
      <div 
        className={cn(
          "md:hidden fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-40 transition-opacity duration-300",
          isOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        )}
        onClick={() => setIsOpen(false)}
      />

      <aside className={cn(
        "fixed md:relative top-0 left-0 h-screen bg-slate-900 border-r border-slate-800 flex flex-col z-50 transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]",
        isOpen ? "w-64 translate-x-0" : "w-64 md:w-20 -translate-x-full md:translate-x-0"
      )}>
        <button 
          onClick={() => setIsOpen(!isOpen)}
          className="hidden md:flex absolute -right-3 top-8 bg-slate-800 border border-slate-700 rounded-full p-1 text-slate-400 hover:text-white z-50 hover:bg-slate-700 transition-colors focus:ring-2 focus:ring-accent-500/50 outline-none"
        >
          {isOpen ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>

        <div className={cn("p-6 flex items-center gap-3 border-b border-slate-800", isOpen ? "" : "justify-center px-0")}>
          <div className="w-10 h-10 bg-accent-500 rounded-xl flex items-center justify-center shadow-lg shadow-accent-500/20 shrink-0">
            <Activity className="text-slate-950 w-6 h-6" />
          </div>
          {isOpen && (
            <div className="overflow-hidden whitespace-nowrap">
              <h1 className="text-lg font-bold tracking-tight text-white whitespace-nowrap">ARBIX <span className="text-accent-500">QUANTUM</span></h1>
              <p className="text-xs uppercase tracking-widest text-slate-400 font-medium">P2P Engine v5.0</p>
            </div>
          )}
        </div>

        <nav className="flex-1 p-4 overflow-x-hidden overflow-y-auto">
          {navGroups.map((group, groupIndex) => (
            <div key={group.title} className={cn(groupIndex > 0 && (isOpen ? 'mt-6' : 'mt-4'))}>
              {/* У згорнутому стані заголовок не вміщається — його роль
                  бере на себе розділювальна лінія. */}
              {isOpen ? (
                <div className="px-4 mb-2 text-[10px] font-bold uppercase tracking-widest text-slate-600">
                  {group.title}
                </div>
              ) : (
                groupIndex > 0 && <div className="mx-3 mb-4 border-t border-slate-800" />
              )}

              <div className="space-y-1">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <NavLink
                      key={item.id}
                      to={item.path}
                      end={item.path === '/app'}
                      className={({ isActive }) => cn(
                        "w-full flex items-center rounded-xl transition-all font-bold text-sm focus:ring-2 focus:ring-accent-500/50 outline-none",
                        isOpen ? "px-4 py-2.5 gap-3" : "p-3 justify-center",
                        isActive
                          ? "bg-accent-500/10 text-accent-400 border border-accent-500/20"
                          : "text-slate-400 hover:bg-slate-800 hover:text-white border border-transparent"
                      )}
                      title={!isOpen ? item.label : undefined}
                      // На телефоні меню — оверлей: лишати його відкритим після
                      // переходу означає ховати сторінку, на яку щойно перейшли.
                      onClick={() => {
                        if (window.innerWidth < 768) setIsOpen(false);
                      }}
                    >
                      <Icon className="w-5 h-5 shrink-0" />
                      {isOpen && <span className="whitespace-nowrap">{item.label}</span>}
                    </NavLink>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="p-4 border-t border-slate-800 space-y-2 overflow-x-hidden">

          {/* Повернення на публічний сайт: із дашборду туди не було
              жодного шляху, крім ручного правлення адреси. */}
          <NavLink
            to="/"
            className={cn(
              "w-full flex items-center rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition-all font-bold text-sm focus:ring-2 focus:ring-accent-500/50 outline-none",
              isOpen ? "px-4 py-3 gap-3" : "p-3 justify-center"
            )}
            title={!isOpen ? "На головну" : undefined}
          >
            <Home className="w-5 h-5 shrink-0" />
            {isOpen && <span className="whitespace-nowrap">На головну</span>}
          </NavLink>

          <button
            onClick={onLogout}
            className={cn(
              "w-full flex items-center rounded-xl text-slate-400 hover:bg-red-500/10 hover:text-red-400 transition-all font-bold text-sm focus:ring-2 focus:ring-red-500/50 outline-none",
              isOpen ? "px-4 py-3 gap-3" : "p-3 justify-center"
            )}
            title={!isOpen ? "Вийти" : undefined}
          >
            <LogOut className="w-5 h-5 shrink-0" />
            {isOpen && <span className="whitespace-nowrap">Вийти</span>}
          </button>

          <div
            className={cn(
              "mt-4 flex items-center justify-center rounded-xl p-3 border transition-all",
              connectionView.box
            )}
            title={connectionView.title}
          >
            <div className="relative flex items-center justify-center">
              <div className={cn("w-2.5 h-2.5 rounded-full", connectionView.dot)} />
              {connection === 'live' && (
                <div className="absolute w-2.5 h-2.5 rounded-full bg-accent-500 animate-ping opacity-75" />
              )}
            </div>
            {isOpen && (
              <span className="ml-3 text-xs font-bold uppercase tracking-wider whitespace-nowrap">
                {connectionView.label}
              </span>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
