import React, { useState } from 'react';
import useSWR from 'swr';
import { Key, Eye, EyeOff, CheckCircle2, Trash2, Bot } from 'lucide-react';
import { useExchanges } from '../hooks/useExchanges';
import { ApiKeyConfig } from '../types';
import { cn } from '../lib/utils';
import { motion } from 'motion/react';
import { useAppStore } from '../store';
import { api } from '../services/api';
import { toast } from 'sonner';
import { doc, setDoc } from 'firebase/firestore';
import { db, auth } from '../firebase';

/**
 * Біржі, які автентифікуються персональними ключами.
 *
 * Список повторює core/exchange_names.py. «Telegram Wallet» звідси
 * прибрано не тому, що біржі немає, а тому що вона зветься `Wallet`:
 * назва їхала на бекенд як `telegram wallet` і лягала в базу під
 * написанням, за яким сканер креденшли вже не шукав.
 */
const EXCHANGES: { name: string; hasPassphrase?: boolean; hint?: string }[] = [
  { name: 'Binance' },
  { name: 'Bybit' },
  { name: 'OKX', hasPassphrase: true },
  { name: 'MEXC' },
  { name: 'BingX' },
  { name: 'Wallet', hint: 'Telegram Wallet: @wallet → P2P → Settings → API. Потрібен лише API Key.' },
];

/**
 * Біржі, які працюють без персональних ключів.
 *
 * CryptoBot тут не «не підтримується» — сканер його опитує нарівні з
 * рештою (core/engine/exchange_manager.ALL_EXCHANGES). Просто авторизація
 * в нього інша: Pyrogram-юзербот на сервері бота отримує tgWebAppData і
 * оновлює токен раз на кілька хвилин. Вводити API Key і Secret нема чого —
 * тому картка з полями, яка тут була раніше, нікуди не вела: ключі лягали
 * в базу під назвою «Cryptobot», яку не читає жоден клієнт.
 */
interface UserbotExchangeCardProps {
  name: string;
  note: string;
}

const USERBOT_EXCHANGES: UserbotExchangeCardProps[] = [
  {
    name: 'CryptoBot',
    note: 'Підключається юзерботом на сервері: TELEGRAM_API_ID, TELEGRAM_API_HASH і сесія в data/cryptobot_session. Персональні ключі не потрібні й не приймаються.',
  },
];

export default function ApiKeysPanel() {
  const { userSettings, setUserSettings } = useAppStore();
  // Особа з підтвердженої сесії, а не з поля вводу в налаштуваннях.
  const telegramId = useAppStore(state => state.auth?.telegramId);

  // Джерело правди — зашифроване сховище бота, а не браузер.
  // Список підключених бірж тягнемо з /telegram/sync.
  const { data: sync, mutate } = useSWR(
    telegramId ? ['/telegram/sync', telegramId] : null,
    () => api.syncTelegram(telegramId!),
    { shouldRetryOnError: false }
  );
  // Звіряємось без урахування регістру: бекенд віддає канонічні назви
  // ("OKX", "BingX"), і посимвольне порівняння з нашим списком ламалось би
  // на першій же біржі, написаній не так.
  const connectedExchanges = (sync?.keys ?? []).map(k => k.toLowerCase());

  /**
   * Кладе ключі в бота (там вони шифруються Fernet перед записом у БД).
   *
   * Раніше виклик api.saveCredentials був закоментований: ключі летіли
   * в Firestore і до бота не доходили взагалі. Секрети бірж у Firestore
   * лежали відкритим текстом — тому їх тут більше не зберігаємо.
   */
  const handleSaveKey = async (exchange: string, config: ApiKeyConfig) => {
    if (!telegramId) {
      toast.error('Потрібен вхід через Telegram — без нього невідомо, чиї це ключі');
      throw new Error('telegramUserId is not set');
    }

    await api.saveCredentials(exchange, config, telegramId);
    await mutate();

    // Локально лишаємо тільки факт підключення, без секретів.
    const newApiKeys = { ...(userSettings.apiKeys || {}) };
    delete newApiKeys[exchange.toLowerCase()];
    setUserSettings({ ...userSettings, apiKeys: newApiKeys });

    if (auth.currentUser) {
      await setDoc(doc(db, 'users', auth.currentUser.uid), { apiKeys: newApiKeys }, { merge: true });
    }

    toast.success(`Ключі ${exchange} збережено в боті`);
  };

  const handleDisconnect = async (exchange: string) => {
    if (!telegramId) {
      toast.error('Немає Telegram ID — нема кого відв\'язувати');
      return;
    }

    try {
      await api.deleteCredentials(exchange, telegramId);
      await mutate();
      toast.success(`${exchange} відв'язано`);
    } catch (error: any) {
      if (error?.status === 404) {
        await mutate();
        toast.info(`${exchange} і так не був підключений`);
        return;
      }
      toast.error(`Не вдалось відв'язати ${exchange}: ${error?.message ?? 'помилка'}`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-accent-500/10 border border-accent-500/20 rounded-3xl p-6 mb-8">
        <div className="flex items-start gap-4">
          <div className="p-2 bg-accent-500/20 rounded-xl">
            <Key className="w-6 h-6 text-accent-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-accent-400 mb-1">Ключі бірж</h2>
            <p className="text-sm text-accent-500/80 leading-relaxed">
              Ключі потрібні для читання балансів, профілю мерчанта й торгівлі.
              Вони шифруються (Fernet) перед записом у базу бота — у браузері
              не лишається нічого. Це те саме сховище, що й у команди
              <code className="mx-1 text-accent-300">/connect</code> у Telegram.
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {EXCHANGES.map(ex => (
          <ApiKeyCard
            key={ex.name}
            exchange={ex.name}
            hint={ex.hint}
            isConnected={connectedExchanges.includes(ex.name.toLowerCase())}
            hasPassphrase={ex.hasPassphrase}
            onSave={(config: any) => handleSaveKey(ex.name, config)}
            onDisconnect={() => handleDisconnect(ex.name)}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {USERBOT_EXCHANGES.map(ex => (
          <UserbotExchangeCard key={ex.name} name={ex.name} note={ex.note} />
        ))}
      </div>
    </div>
  );
}

/**
 * Біржа без персональних ключів. Показуємо реальний стан із /exchanges,
 * щоб було видно, чи сканер її взагалі опитує — це єдине, чим тут можна
 * керувати, і робиться воно в «Моніторингу».
 */
// React.FC, а не звичайна функція: інакше TS рахує `key` зайвим пропом
// (той самий патерн, що в ExchangeChip у dashboard/ExchangeHealth.tsx).
const UserbotExchangeCard: React.FC<UserbotExchangeCardProps> = ({ name, note }) => {
  const { exchanges } = useExchanges();
  const status = exchanges.find(e => e.name.toLowerCase() === name.toLowerCase());

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-3xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <ExchangeIcon name={name} />
          <div>
            <h3 className="font-bold text-white text-lg">{name}</h3>
            <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold">
              Без персональних ключів
            </p>
          </div>
        </div>

        {status && (
          <span
            className={cn(
              'px-2.5 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider border',
              status.enabled
                ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                : status.isCooldown
                  ? 'bg-orange-500/10 border-orange-500/30 text-orange-400'
                  : 'bg-slate-800 border-slate-700 text-slate-400'
            )}
          >
            {status.enabled ? 'Опитується' : status.isCooldown ? 'Пауза' : 'Вимкнено'}
          </span>
        )}
      </div>

      <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50 flex items-start gap-3">
        <Bot className="w-5 h-5 text-sky-400 shrink-0 mt-0.5" />
        <p className="text-[11px] text-slate-400 leading-snug">{note}</p>
      </div>
    </div>
  );
};

function ApiKeyCard({ exchange, isConnected, onSave, onDisconnect, hasPassphrase, hint }: any) {
  const [isEditing, setIsEditing] = useState(!isConnected);
  const [showSecret, setShowSecret] = useState(false);
  const [localKeys, setLocalKeys] = useState<ApiKeyConfig>({ key: '', secret: '', passphrase: '' });
  const [isSaving, setIsSaving] = useState(false);

  // Wallet автентифікується одним X-API-Key — секрету в нього просто немає,
  // і вимагати його означало б зробити форму незаповнюваною.
  const needsSecret = exchange !== 'Wallet';

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await onSave(localKeys);
      setIsEditing(false);
    } catch (err) {
      console.error("Failed to save credentials", err);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className={cn(
      "bg-slate-900 border rounded-3xl p-6 transition-all",
      isConnected && !isEditing ? "border-accent-500/30" : "border-slate-800"
    )}>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ExchangeIcon name={exchange} />
          <div>
            <h3 className="font-bold text-white text-lg">{exchange}</h3>
            <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold">
              {isConnected ? 'Підключено' : 'Не підключено'}
            </p>
          </div>
        </div>
        {!isEditing && (
          <div className="flex items-center gap-2">
            <motion.button 
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={() => setIsEditing(true)} 
              className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold rounded-lg transition-colors focus:ring-2 focus:ring-slate-500/50 outline-none"
            >
              UPDATE
            </motion.button>
            <motion.button 
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={onDisconnect} 
              className="p-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg transition-colors focus:ring-2 focus:ring-red-500/50 outline-none"
              title="Disconnect Exchange"
            >
              <Trash2 className="w-4 h-4" />
            </motion.button>
          </div>
        )}
      </div>

      {isEditing ? (
        <div className="space-y-4">
          {hint && (
            <p className="text-[11px] text-slate-500 leading-snug">{hint}</p>
          )}
          <div>
            <label className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">API Key</label>
            <input
              type="text"
              value={localKeys.key}
              onChange={(e) => setLocalKeys({ ...localKeys, key: e.target.value })}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all"
              placeholder="Встав API Key"
            />
          </div>
          {needsSecret && (
            <div>
              <label className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">API Secret</label>
              <div className="relative">
                <input
                  type={showSecret ? "text" : "password"}
                  value={localKeys.secret}
                  onChange={(e) => setLocalKeys({ ...localKeys, secret: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all pr-10"
                  placeholder="Встав API Secret"
                />
                <button
                  onClick={() => setShowSecret(!showSecret)}
                  className="absolute right-3 top-3 text-slate-400 hover:text-slate-300 focus:outline-none"
                >
                  {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
          )}
          {hasPassphrase && (
            <div>
              <label className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Passphrase</label>
              <input
                type="password"
                value={localKeys.passphrase}
                onChange={(e) => setLocalKeys({ ...localKeys, passphrase: e.target.value })}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all"
                placeholder="Встав Passphrase"
              />
            </div>
          )}
          <div className="pt-2 flex gap-3">
            {isConnected && (
              <motion.button
                whileHover={{ scale: 1.02 }}
                whileTap={{ scale: 0.95 }}
                onClick={() => setIsEditing(false)}
                className="flex-1 py-2.5 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all focus:ring-2 focus:ring-slate-500/50 outline-none"
              >
                Скасувати
              </motion.button>
            )}
            <motion.button
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.95 }}
              onClick={handleSave}
              disabled={isSaving || !localKeys.key || (needsSecret && !localKeys.secret)}
              className="flex-1 py-2.5 bg-accent-500 hover:bg-accent-400 text-slate-950 text-xs font-bold rounded-xl transition-all shadow-lg shadow-accent-500/20 disabled:opacity-50 focus:ring-2 focus:ring-accent-500/50 outline-none"
            >
              {isSaving ? 'Зберігаю…' : 'Зберегти ключі'}
            </motion.button>
          </div>
        </div>
      ) : (
        <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <CheckCircle2 className="w-5 h-5 text-accent-500" />
            <div className="text-sm font-mono text-slate-400">
              ••••••••••••••••••••
            </div>
          </div>
          <div className="text-xs font-bold text-accent-500 uppercase tracking-widest bg-accent-500/10 px-2 py-1 rounded">
            Активні
          </div>
        </div>
      )}
    </div>
  );
}

function ExchangeIcon({ name }: { name: string }) {
  // Ключі — канонічні назви бірж (core/exchange_names.py). Раніше тут
  // стояло 'Telegram Wallet', якого в системі не існує, тож біржа Wallet
  // завжди малювалась сірою заглушкою.
  const colors: Record<string, string> = {
    'Bybit': 'bg-orange-500',
    'OKX': 'bg-white',
    'Binance': 'bg-yellow-400',
    'MEXC': 'bg-blue-500',
    'BingX': 'bg-cyan-400',
    'CryptoBot': 'bg-indigo-500 text-white',
    'Wallet': 'bg-sky-500 text-white',
  };
  return (
    <div className={cn("w-10 h-10 rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-xs text-slate-950", colors[name] || 'bg-slate-700 text-white')}>
      {name[0]}
    </div>
  );
}
