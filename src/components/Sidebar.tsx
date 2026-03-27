import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Activity, Key, Settings, ShieldBan, LogOut, LogIn, ShieldAlert, ChevronLeft, ChevronRight, BarChart3, Wallet } from 'lucide-react';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';

interface SidebarProps {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
  user: any;
  onLogin: () => void;
  onLogout: () => void;
}

export default function Sidebar({ isOpen, setIsOpen, user, onLogin, onLogout }: SidebarProps) {
  const { isAdmin, setIsAdmin, isConnected } = useAppStore();

  const navItems = [
    { id: 'dashboard', path: '/', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'autotrade', path: '/autotrade', label: 'Auto-Trade', icon: Activity },
    { id: 'accounts', path: '/accounts', label: 'Accounts', icon: Wallet },
    { id: 'analytics', path: '/analytics', label: 'Analytics', icon: BarChart3 },
    { id: 'apikeys', path: '/apikeys', label: 'API Keys', icon: Key },
    { id: 'settings', path: '/settings', label: 'Settings', icon: Settings },
    { id: 'blacklist', path: '/blacklist', label: 'Blacklist', icon: ShieldBan },
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
          className="hidden md:flex absolute -right-3 top-8 bg-slate-800 border border-slate-700 rounded-full p-1 text-slate-400 hover:text-white z-50 hover:bg-slate-700 transition-colors focus:ring-2 focus:ring-emerald-500/50 outline-none"
        >
          {isOpen ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>

        <div className={cn("p-6 flex items-center gap-3 border-b border-slate-800", isOpen ? "" : "justify-center px-0")}>
          <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center shadow-lg shadow-emerald-500/20 shrink-0">
            <Activity className="text-slate-950 w-6 h-6" />
          </div>
          {isOpen && (
            <div className="overflow-hidden whitespace-nowrap">
              <h1 className="text-xl font-bold tracking-tight text-white">ARBIX <span className="text-emerald-500">QUANTUM</span></h1>
              <p className="text-xs uppercase tracking-widest text-slate-400 font-medium">P2P Engine v5.0</p>
            </div>
          )}
        </div>

        <nav className="flex-1 p-4 space-y-2 overflow-x-hidden">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.id}
                to={item.path}
                className={({ isActive }) => cn(
                  "w-full flex items-center rounded-xl transition-all font-bold text-sm focus:ring-2 focus:ring-emerald-500/50 outline-none",
                  isOpen ? "px-4 py-3 gap-3" : "p-3 justify-center",
                  isActive 
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" 
                    : "text-slate-400 hover:bg-slate-800 hover:text-white border border-transparent"
                )}
                title={!isOpen ? item.label : undefined}
              >
                <Icon className="w-5 h-5 shrink-0" />
                {isOpen && <span className="whitespace-nowrap">{item.label}</span>}
              </NavLink>
            );
          })}
        </nav>

        <div className="p-4 border-t border-slate-800 space-y-2 overflow-x-hidden">
          {user && (
            <button 
              onClick={() => setIsAdmin(!isAdmin)}
              className={cn(
                "w-full flex items-center rounded-xl transition-all font-bold text-sm border focus:ring-2 focus:ring-orange-500/50 outline-none",
                isOpen ? "justify-between px-4 py-3" : "justify-center p-3",
                isAdmin 
                  ? "bg-orange-500/10 text-orange-400 border-orange-500/20" 
                  : "bg-slate-800 text-slate-400 border-transparent hover:bg-slate-700"
              )}
              title={!isOpen ? "Admin Mode" : undefined}
            >
              <div className={cn("flex items-center", isOpen ? "gap-3" : "")}>
                <ShieldAlert className="w-5 h-5 shrink-0" />
                {isOpen && <span className="whitespace-nowrap">Admin Mode</span>}
              </div>
              {isOpen && (
                <div className={cn(
                  "w-8 h-4 rounded-full transition-colors relative shrink-0",
                  isAdmin ? "bg-orange-500" : "bg-slate-600"
                )}>
                  <div className={cn(
                    "absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all",
                    isAdmin ? "right-0.5" : "left-0.5"
                  )} />
                </div>
              )}
            </button>
          )}

          {user ? (
            <button 
              onClick={onLogout}
              className={cn(
                "w-full flex items-center rounded-xl text-slate-400 hover:bg-red-500/10 hover:text-red-400 transition-all font-bold text-sm focus:ring-2 focus:ring-red-500/50 outline-none",
                isOpen ? "px-4 py-3 gap-3" : "p-3 justify-center"
              )}
              title={!isOpen ? "Logout" : undefined}
            >
              <LogOut className="w-5 h-5 shrink-0" />
              {isOpen && <span className="whitespace-nowrap">Logout</span>}
            </button>
          ) : (
            <button 
              onClick={onLogin}
              className={cn(
                "w-full flex items-center rounded-xl text-slate-400 hover:bg-emerald-500/10 hover:text-emerald-400 transition-all font-bold text-sm focus:ring-2 focus:ring-emerald-500/50 outline-none",
                isOpen ? "px-4 py-3 gap-3" : "p-3 justify-center"
              )}
              title={!isOpen ? "Login" : undefined}
            >
              <LogIn className="w-5 h-5 shrink-0" />
              {isOpen && <span className="whitespace-nowrap">Login</span>}
            </button>
          )}

          <div className={cn(
            "mt-4 flex items-center justify-center rounded-xl p-3 border transition-all",
            isConnected === true 
              ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400" 
              : isConnected === false 
                ? "bg-red-500/10 border-red-500/20 text-red-400"
                : "bg-slate-800 border-slate-700 text-slate-400"
          )}
          title={isConnected === true ? "Backend Connected" : isConnected === false ? "Backend Disconnected (Using Mock Data)" : "Connecting..."}
          >
            <div className="relative flex items-center justify-center">
              <div className={cn(
                "w-2.5 h-2.5 rounded-full",
                isConnected === true ? "bg-emerald-500" : isConnected === false ? "bg-red-500" : "bg-slate-500 animate-pulse"
              )} />
              {isConnected === true && (
                <div className="absolute w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping opacity-75" />
              )}
            </div>
            {isOpen && (
              <span className="ml-3 text-xs font-bold uppercase tracking-wider whitespace-nowrap">
                {isConnected === true ? "Live Data" : isConnected === false ? "Mock Data" : "Connecting..."}
              </span>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
