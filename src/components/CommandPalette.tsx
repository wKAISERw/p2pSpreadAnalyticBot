import { useEffect, useState } from 'react';
import { Command } from 'cmdk';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../store';
import { Search, LayoutDashboard, Settings, Key, ShieldBan, Activity, Volume2, VolumeX, Power, PowerOff } from 'lucide-react';

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { userSettings, setUserSettings, globalSettings, setGlobalSettings, isAdmin } = useAppStore();

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
            placeholder="Type a command or search..."
          />
        </div>
        <Command.List className="max-h-[300px] overflow-y-auto p-2">
          <Command.Empty className="py-6 text-center text-sm text-zinc-500">
            No results found.
          </Command.Empty>

          <Command.Group heading="Navigation" className="text-xs font-medium text-zinc-500 px-2 py-1.5">
            <Command.Item
              onSelect={() => runCommand(() => navigate('/'))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              <LayoutDashboard className="mr-2 h-4 w-4" />
              Dashboard
            </Command.Item>
            <Command.Item
              onSelect={() => runCommand(() => navigate('/autotrade'))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              <Activity className="mr-2 h-4 w-4" />
              Auto-Trade
            </Command.Item>
            <Command.Item
              onSelect={() => runCommand(() => navigate('/analytics'))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              <Activity className="mr-2 h-4 w-4" />
              Analytics
            </Command.Item>
            <Command.Item
              onSelect={() => runCommand(() => navigate('/apikeys'))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              <Key className="mr-2 h-4 w-4" />
              API Keys
            </Command.Item>
            <Command.Item
              onSelect={() => runCommand(() => navigate('/blacklist'))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              <ShieldBan className="mr-2 h-4 w-4" />
              Blacklist
            </Command.Item>
            <Command.Item
              onSelect={() => runCommand(() => navigate('/settings'))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              <Settings className="mr-2 h-4 w-4" />
              Settings
            </Command.Item>
          </Command.Group>

          <Command.Group heading="Quick Actions" className="text-xs font-medium text-zinc-500 px-2 py-1.5 mt-2">
            <Command.Item
              onSelect={() => runCommand(() => setUserSettings({ ...userSettings, soundEnabled: !userSettings.soundEnabled }))}
              className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
            >
              {userSettings.soundEnabled ? <VolumeX className="mr-2 h-4 w-4" /> : <Volume2 className="mr-2 h-4 w-4" />}
              {userSettings.soundEnabled ? 'Mute Sounds' : 'Enable Sounds'}
            </Command.Item>
            
            {isAdmin && (
              <Command.Item
                onSelect={() => runCommand(() => setGlobalSettings({ ...globalSettings, riskMode: globalSettings.riskMode === 'STRICT' ? 'WARNING' : 'STRICT' }))}
                className="flex cursor-pointer items-center rounded-md px-2 py-2 text-sm text-zinc-100 hover:bg-zinc-800 aria-selected:bg-zinc-800"
              >
                {globalSettings.riskMode === 'STRICT' ? <PowerOff className="mr-2 h-4 w-4 text-red-400" /> : <Power className="mr-2 h-4 w-4 text-emerald-400" />}
                {globalSettings.riskMode === 'STRICT' ? 'Relax Risk Mode' : 'Strict Risk Mode'}
              </Command.Item>
            )}
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  );
}
