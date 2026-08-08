import { useEffect, useState } from 'react';
import { Command } from 'cmdk';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { useAppStore } from '../store';
import { api } from '../services/api';
import {
  Search, LayoutDashboard, Settings, Key, ShieldBan, Activity, Volume2, VolumeX,
  Power, PowerOff, BarChart3, SlidersHorizontal, CreditCard, MonitorDot, Wallet,
} from 'lucide-react';

// Ті самі розділи, що в сайдбарі. Раніше половини з них тут не було —
// «Фільтри», «Картки», «Моніторинг» і «Баланси» не відкривались із палітри.
const NAV_ITEMS = [
  { path: '/app', label: 'Дашборд', icon: LayoutDashboard },
  { path: '/app/analytics', label: 'Аналітика', icon: BarChart3 },
  { path: '/app/filters', label: 'Фільтри', icon: SlidersHorizontal },
  { path: '/app/blacklist', label: 'Чорний список', icon: ShieldBan },
  { path: '/app/cards', label: 'Картки', icon: CreditCard },
  { path: '/app/accounts', label: 'Баланси бірж', icon: Wallet },
  { path: '/app/apikeys', label: 'Ключі бірж', icon: Key },
  { path: '/app/monitoring', label: 'Моніторинг', icon: MonitorDot },
  { path: '/app/logs', label: 'Логи', icon: Activity },
  { path: '/app/settings', label: 'Налаштування', icon: Settings },
] as const;

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { userSettings, setUserSettings, globalSettings, patchGlobalSettings } = useAppStore();
  const isAdmin = useAppStore(state => state.auth?.isAdmin ?? false);

  /**
   * Раніше ця команда правила лише локальний стор — на сканер вона
   * не впливала ніяк. Тепер пише risk_mode у bot_settings.
   */
  const toggleRiskMode = async () => {
    const next = globalSettings.riskMode === 'STRICT' ? 'WARNING' : 'STRICT';
    const previous = globalSettings.riskMode;
    patchGlobalSettings({ riskMode: next });
    try {
      await api.updateGlobalSettings({ riskMode: next });
      toast.success(`Рівень антифроду: ${next}`);
    } catch (error: any) {
      patchGlobalSettings({ riskMode: previous });
      toast.error(`Не збережено: ${error?.message ?? 'помилка'}`);
    }
  };

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((open) => !open);
      }
    };

    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, []);

  const runCommand = (command: () => void) => {
    setOpen(false);
    command();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh] bg-black/50 backdrop-blur-sm">
      <div className="fixed inset-0" onClick={() => setOpen(false)} />
      <Command
        className="relative z-50 w-full max-w-lg overflow-hidden rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl"
        label="Global Command Menu"
      >
        <div className="flex items-center border-b border-zinc-800 px-3">
          <Search className="mr-2 h-5 w-5 shrink-0 text-zinc-500" />
          <Command.Input
            autoFocus
            className="flex h-12 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-zinc-500 text-zinc-100"
            placeholder="Куди йдемо? Почни вводити…"
          />
        </div>
        <Command.List className="max-h-[300px] overflow-y-auto p-2">
          <Command.Empty className="py-6 text-center text-sm text-zinc-500">
            Нічого не знайдено.
          </Command.Empty>

          <Command.Group heading="Розділи" className="text-xs font-medium text-zinc-500 px-2 py-1.5">
            {NAV_ITEMS.map(({ path, label, icon: Icon }) => (
              <Command.Item
                key={path}
                value={label}
                onSelect={() => runCommand(() => navigate(path))}
                className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
              >
                <Icon className="mr-2 h-4 w-4" />
                {label}
              </Command.Item>
            ))}
          </Command.Group>

          <Command.Group heading="Швидкі дії" className="text-xs font-medium text-zinc-500 px-2 py-1.5 mt-2">
            <Command.Item
              value={userSettings.soundEnabled ? 'Вимкнути звук' : 'Увімкнути звук'}
              onSelect={() => runCommand(() => setUserSettings({ ...userSettings, soundEnabled: !userSettings.soundEnabled }))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              {userSettings.soundEnabled ? <VolumeX className="mr-2 h-4 w-4" /> : <Volume2 className="mr-2 h-4 w-4" />}
              {userSettings.soundEnabled ? 'Вимкнути звук алертів' : 'Увімкнути звук алертів'}
            </Command.Item>

            {isAdmin && (
              <Command.Item
                value="Рівень антифроду"
                onSelect={() => runCommand(toggleRiskMode)}
                className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
              >
                {globalSettings.riskMode === 'STRICT' ? <PowerOff className="mr-2 h-4 w-4 text-red-400" /> : <Power className="mr-2 h-4 w-4 text-accent-400" />}
                {globalSettings.riskMode === 'STRICT' ? 'Послабити антифрод (WARNING)' : 'Посилити антифрод (STRICT)'}
              </Command.Item>
            )}
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}
