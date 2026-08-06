import React, {useEffect, useRef, useState} from 'react';
import {
  Settings, ShieldAlert, CheckCircle2, Filter, Shield, Bot, Zap,
  Pin, MessageSquare, Bell, Send, Download, Upload, RefreshCw,
  Volume2, VolumeX, DollarSign, Percent, Building2, Link2, Unlink,
  ArrowDownToLine, Trash2, AlertTriangle, Users, Database, Palette, CreditCard, FlaskConical
} from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { cn } from '../lib/utils';
import { motion } from 'motion/react';
import { useAppStore } from '../store';
import { api } from '../services/api';
import { toast } from 'sonner';
import { GlobalSettings } from '../types';
import AccountSection from './settings/AccountSection';
import DisplaySettingsSection from './settings/DisplaySettingsSection';
import CardDisplaySection from './settings/CardDisplaySection';
import AppearanceSection from './settings/AppearanceSection';
import SyncSection from './settings/SyncSection';
import FeaturesSection from './settings/FeaturesSection';
import BankLimitsSection from './settings/BankLimitsSection';

type TabId = 'account' | 'appearance' | 'alerts' | 'cards' | 'extra' | 'antifraud';

const TABS: { id: TabId; label: string; icon: React.ElementType; adminOnly?: boolean }[] = [
  { id: 'account', label: 'Акаунт', icon: Users },
  { id: 'appearance', label: 'Вигляд і звук', icon: Palette },
  { id: 'alerts', label: 'Сповіщення', icon: Bell },
  { id: 'cards', label: 'Картки й ліміти', icon: CreditCard },
  { id: 'extra', label: 'Експеримент', icon: FlaskConical },
  { id: 'antifraud', label: 'Антифрод', icon: ShieldAlert, adminOnly: true },
];

/**
 * Налаштувань стало вісім секцій — суцільною стрічкою це кілька екранів
 * прокрутки без жодного орієнтиру. Розкладено по вкладках; активна
 * тримається в URL (?tab=), щоб посилання й перезавантаження не скидали
 * тебе на початок.
 */
export default function SettingsPanel() {
  const isAdmin = useAppStore(state => state.auth?.isAdmin ?? false);
  const [searchParams, setSearchParams] = useSearchParams();

  const visibleTabs = TABS.filter(tab => !tab.adminOnly || isAdmin);
  const requested = searchParams.get('tab') as TabId | null;
  const active: TabId = visibleTabs.some(t => t.id === requested) ? requested! : 'account';

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Налаштування</h1>
        <p className="text-sm text-slate-400">Акаунт, вигляд, сповіщення та керування сканером</p>
      </div>

      <div className="flex gap-1 overflow-x-auto pb-1 -mx-1 px-1 [&::-webkit-scrollbar]:hidden [scrollbar-width:none]">
        {visibleTabs.map(tab => {
          const Icon = tab.icon;
          const isActive = active === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setSearchParams({ tab: tab.id }, { replace: true })}
              className={cn(
                'flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold whitespace-nowrap transition-colors border shrink-0',
                isActive
                  ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                  : 'bg-slate-900/50 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
              )}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* key на вкладці — щоб перехід між ними мав ту саму появу,
          що й решта інтерфейсу, а не різкий підмін контенту. */}
      <div key={active} className="space-y-6 animate-rise">
        {active === 'account' && (
          <>
            <AccountSection />
            <SyncSection />
          </>
        )}
        {active === 'appearance' && (
          <>
            <AppearanceSection />
            <SoundSettings />
          </>
        )}
        {active === 'alerts' && <DisplaySettingsSection />}
        {active === 'cards' && (
          <>
            <CardDisplaySection />
            <BankLimitsSection />
          </>
        )}
        {active === 'extra' && <FeaturesSection />}
        {active === 'antifraud' && isAdmin && <AdminSettings />}
      </div>
    </div>
  );
}

function SoundSettings() {
  const { userSettings, setUserSettings } = useAppStore();

  // Пишемо лише в стор: у хмару це поїде через useCloudPrefs, якщо
  // прив'язаний Google. Раніше тут був прямий запис у Firestore — він
  // дублював синхронізацію і тягнув firebase у цей чанк статично.
  const toggleSound = () =>
    setUserSettings({ ...userSettings, soundEnabled: !userSettings.soundEnabled });

  const changeVolume = (val: number) =>
    setUserSettings({ ...userSettings, soundVolume: val });

  return (
    <section className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6">
      <div className="flex items-center gap-4 mb-6">
        <div className="p-3 bg-indigo-500/20 rounded-xl">
          <Volume2 className="w-6 h-6 text-indigo-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-white">Звук алертів</h2>
          <p className="text-sm text-slate-400">Сигнал про новий спред</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-bold text-slate-300">Увімкнути звук</span>
            <button
              onClick={toggleSound} // <--- ЗМІНЕНО ТУТ
              className={cn(
                "w-12 h-6 rounded-full transition-colors relative",
                userSettings.soundEnabled ? "bg-indigo-500" : "bg-slate-700"
              )}
            >
              <motion.div
                className="w-4 h-4 bg-white rounded-full absolute top-1"
                animate={{ left: userSettings.soundEnabled ? '26px' : '4px' }}
                transition={{ type: "spring", stiffness: 500, damping: 30 }}
              />
            </button>
          </div>
          <p className="text-xs text-slate-500">Програється, коли з'являється спред не нижчий за твій мінімум.</p>
        </div>

        <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm font-bold text-slate-300">Гучність</span>
            <span className="text-xs font-medium text-indigo-400 bg-indigo-500/10 px-2 py-1 rounded-md">
              {Math.round((userSettings.soundVolume || 0.5) * 100)}%
            </span>
          </div>
          <div className="flex items-center gap-3">
            <VolumeX className="w-4 h-4 text-slate-500" />
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={userSettings.soundVolume || 0.5}
              onChange={(e) => changeVolume(parseFloat(e.target.value))} // <--- ЗМІНЕНО ТУТ
              className="w-full accent-indigo-500"
              disabled={!userSettings.soundEnabled}
            />
            <Volume2 className="w-4 h-4 text-slate-400" />
          </div>
        </div>
      </div>
    </section>
  );
}

function AdminSettings() {
  const globalSettings = useAppStore(state => state.globalSettings);
  const globalSettingsLoaded = useAppStore(state => state.globalSettingsLoaded);
  const patchGlobalSettings = useAppStore(state => state.patchGlobalSettings);

  /**
   * Пише ОДИН змінений ключ у bot_settings через /settings/global.
   *
   * Раніше сюди летів увесь об'єкт globalSettings, який брався з мок-значень
   * у сторі й ніколи не читався з бекенда: перша ж зміна будь-якого поля
   * затирала реальний конфіг сканера шістьма значеннями «з голови».
   */
  const handleGlobalChange = async (key: keyof GlobalSettings, value: any) => {
    const previous = globalSettings[key];
    patchGlobalSettings({ [key]: value } as Partial<GlobalSettings>);

    try {
      const result = await api.updateGlobalSettings({ [key]: value } as Partial<GlobalSettings>);
      if (result.rejected?.length) {
        patchGlobalSettings({ [key]: previous } as Partial<GlobalSettings>);
        toast.error(`Бекенд відхилив ключ: ${result.rejected.join(', ')}`);
      }
    } catch (error: any) {
      patchGlobalSettings({ [key]: previous } as Partial<GlobalSettings>);
      toast.error(`Не збережено: ${error?.message ?? 'помилка запиту'}`);
    }
  };

  if (!globalSettingsLoaded) {
    return (
      <section className="bg-slate-900 border border-orange-500/20 rounded-3xl p-6">
        <div className="flex items-center gap-3 text-slate-400 text-sm">
          <RefreshCw className="w-4 h-4 animate-spin" />
          Читаю глобальні налаштування з бота…
        </div>
      </section>
    );
  }

  return (
    <section className="bg-slate-900 border border-orange-500/20 rounded-3xl p-6 relative overflow-hidden">
      <div className="absolute top-0 right-0 w-32 h-32 bg-orange-500/5 rounded-full blur-3xl -mr-10 -mt-10 pointer-events-none"></div>

      <div className="flex items-center gap-3 mb-8">
        <div className="p-2 bg-orange-500/10 rounded-xl border border-orange-500/20">
          <ShieldAlert className="w-5 h-5 text-orange-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-orange-400">Системні налаштування</h2>
          <p className="text-xs uppercase tracking-widest text-orange-500/60 font-semibold">Впливають на поведінку всього сканера</p>
        </div>
      </div>

      <motion.div
        className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"
        initial="hidden"
        animate="visible"
        variants={{ hidden: {}, visible: { transition: { staggerChildren: 0.08 } } }}
      >
        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Shield className="w-4 h-4 text-red-400" />}
          label="Рівень антифроду"
          subLabel="risk_mode"
        >
          <select
            value={globalSettings.riskMode ?? 'WARNING'}
            onChange={(e) => handleGlobalChange('riskMode', e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all"
          >
            <option value="RELAXED">RELAXED</option>
            <option value="WARNING">WARNING</option>
            <option value="STRICT">STRICT</option>
          </select>
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Bot className="w-4 h-4 text-blue-400" />}
          label="Поріг балів ботів"
          subLabel="behavior_alert_score"
        >
          <input
            type="number"
            value={globalSettings.behaviorAlertScore ?? 60}
            onChange={(e) => handleGlobalChange('behaviorAlertScore', parseInt(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Zap className="w-4 h-4 text-yellow-400" />}
          label="Аномальна швидкість (угод/год)"
          subLabel="velocity_spike_per_hour"
        >
          <input
            type="number"
            step="0.1"
            value={globalSettings.velocitySpikePerHour ?? 20}
            onChange={(e) => handleGlobalChange('velocitySpikePerHour', parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Pin className="w-4 h-4 text-red-500" />}
          label="Липкі ліміти (циклів)"
          subLabel="sticky_min_chain"
        >
          <input
            type="number"
            value={globalSettings.stickyMinChain ?? 3}
            onChange={(e) => handleGlobalChange('stickyMinChain', parseInt(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<MessageSquare className="w-4 h-4 text-slate-300" />}
          label="Кеш відгуків (годин)"
          subLabel="review_ttl_hours"
        >
          <input
            type="number"
            step="0.1"
            value={globalSettings.reviewTtlHours ?? 24}
            onChange={(e) => handleGlobalChange('reviewTtlHours', parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Bell className="w-4 h-4 text-yellow-500" />}
          label="Макс. алертів за цикл"
          subLabel="max_alerts_per_cycle"
        >
          <input
            type="number"
            value={globalSettings.maxAlertsPerCycle ?? 5}
            onChange={(e) => handleGlobalChange('maxAlertsPerCycle', parseInt(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        {/* Ключі, які вже були в ALLOWED_KEYS, але яких не існувало в UI */}
        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Percent className="w-4 h-4 text-accent-400" />}
          label="Мін. спред сканера (%)"
          subLabel="min_spread_pct"
        >
          <input
            type="number"
            step="0.1"
            value={globalSettings.minSpreadPct ?? 0.5}
            onChange={(e) => handleGlobalChange('minSpreadPct', parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
        <AdminSettingCard
          icon={<Shield className="w-4 h-4 text-blue-400" />}
          label="Буфер безпеки (%)"
          subLabel="safety_buffer_pct"
        >
          <input
            type="number"
            step="0.1"
            value={globalSettings.safetyBufferPct ?? 0.3}
            onChange={(e) => handleGlobalChange('safetyBufferPct', parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
          />
        </AdminSettingCard>
        </motion.div>

        <AdminToggleCard
          icon={<Link2 className="w-4 h-4 text-purple-400" />}
          label="Вимагати сесії бірж"
          subLabel="require_sessions"
          checked={globalSettings.requireSessions ?? false}
          onChange={(v) => handleGlobalChange('requireSessions', v)}
        />

        <AdminToggleCard
          icon={<MessageSquare className="w-4 h-4 text-slate-300" />}
          label="Логувати знайдені спреди"
          subLabel="show_spread_logs"
          checked={globalSettings.showSpreadLogs ?? true}
          onChange={(v) => handleGlobalChange('showSpreadLogs', v)}
        />

        <AdminToggleCard
          icon={<Users className="w-4 h-4 text-orange-400" />}
          label="Блокувати ФОП / ТОВ"
          subLabel="block_fop_tov"
          checked={globalSettings.blockFopTov ?? false}
          onChange={(v) => handleGlobalChange('blockFopTov', v)}
        />

        <AdminToggleCard
          icon={<Database className="w-4 h-4 text-yellow-400" />}
          label="Блокувати банки/джари"
          subLabel="block_banka_jar"
          checked={globalSettings.blockBankaJar ?? false}
          onChange={(v) => handleGlobalChange('blockBankaJar', v)}
        />
      </motion.div>

      {/* Ваги ризик-движка. До фіксу в api/routers/dashboard.py ці ключі
          відхилялись бекендом завжди — через розбіжність регістру. */}
      <div className="mt-8 pt-6 border-t border-slate-800">
        <div className="flex items-center gap-2 mb-4">
          <ShieldAlert className="w-4 h-4 text-orange-400" />
          <h3 className="text-sm font-bold text-orange-400">Ваги ризик-скорингу</h3>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {RISK_WEIGHTS.map(({ key, label }) => (
            <div key={key} className="bg-slate-950/50 p-3 rounded-xl border border-slate-800/50">
              <div className="text-[11px] font-bold text-white mb-0.5">{label}</div>
              <div className="text-[10px] text-slate-500 font-mono mb-2">{key}</div>
              <input
                type="number"
                step="0.1"
                value={globalSettings[key] ?? 0}
                onChange={(e) => handleGlobalChange(key, parseFloat(e.target.value) || 0)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm font-bold text-white focus:border-orange-500 focus:ring-2 focus:ring-orange-500/50 outline-none transition-all tabular-nums"
              />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const RISK_WEIGHTS: { key: keyof GlobalSettings; label: string }[] = [
  { key: 'WRegex', label: 'Regex' },
  { key: 'WBehavior', label: 'Поведінка' },
  { key: 'WReviewsPct', label: 'Відгуки %' },
  { key: 'WReviewsText', label: 'Відгуки текст' },
  { key: 'WLlm', label: 'LLM' },
  { key: 'WIdentity', label: 'Ідентичність' },
];

function AdminToggleCard({
  icon, label, subLabel, checked, onChange,
}: {
  icon: React.ReactNode;
  label: string;
  subLabel: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <motion.div variants={{ hidden: { opacity: 0, y: 16 }, visible: { opacity: 1, y: 0 } }}>
      <AdminSettingCard icon={icon} label={label} subLabel={subLabel}>
        <button
          onClick={() => onChange(!checked)}
          className={cn(
            'w-full flex items-center justify-between px-4 py-2.5 rounded-xl border text-sm font-bold transition-all focus:ring-2 focus:ring-orange-500/50 outline-none',
            checked
              ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
              : 'bg-slate-950 border-slate-800 text-slate-400'
          )}
        >
          <span>{checked ? 'Увімкнено' : 'Вимкнено'}</span>
          <span className={cn('w-8 h-4 rounded-full relative transition-colors', checked ? 'bg-accent-500' : 'bg-slate-600')}>
            <span
              className={cn(
                'absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all',
                checked ? 'right-0.5' : 'left-0.5'
              )}
            />
          </span>
        </button>
      </AdminSettingCard>
    </motion.div>
  );
}

function AdminSettingCard({ icon, label, subLabel, children }: any) {
  return (
    <div className="bg-slate-950/50 p-4 rounded-2xl border border-slate-800/50">
      <div className="flex items-center gap-2 mb-1">
        {icon}
        <span className="text-sm font-bold text-white">{label}</span>
      </div>
      <div className="text-xs font-mono text-slate-400 mb-3 pl-6">
        └ {subLabel}
      </div>
      {children}
    </div>
  );
}
