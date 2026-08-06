import React, { useState } from 'react';
import { KeyRound, PlugZap, ShieldAlert, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { useAppStore } from '../store';
import { checkConnection, diagnoseApiBase, getApiKey, setApiKey } from '../services/api';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

/**
 * Показується, коли бекенд відповідає 401 або взагалі не відповідає.
 *
 * Бекенд з коміту eb6387b вимагає X-API-Key на всьому /api/v1/*. Ключ
 * зберігається в localStorage цього браузера, а не в бандлі — інакше
 * спільний секрет їхав би кожному, хто відкриє сайт.
 */
export default function ApiAccessBanner() {
  const connection = useAppStore((state) => state.connection);
  const setConnection = useAppStore((state) => state.setConnection);
  const [keyInput, setKeyInput] = useState('');
  const [isChecking, setIsChecking] = useState(false);

  if (connection === 'live' || connection === 'connecting') return null;

  const handleSave = async () => {
    setIsChecking(true);
    if (keyInput.trim()) setApiKey(keyInput.trim());

    const state = await checkConnection();
    setConnection(state);
    setIsChecking(false);

    if (state === 'live') {
      setKeyInput('');
      toast.success("З'єднання з ботом встановлено");
    } else if (state === 'unauthorized') {
      toast.error('Бекенд відхилив ключ');
    } else {
      toast.error(`Бекенд недоступний: ${API_BASE_URL}`);
    }
  };

  const isAuthProblem = connection === 'unauthorized';
  // Якщо запити ріже сам браузер, «недоступний» — хибний діагноз.
  const configProblem = isAuthProblem ? null : diagnoseApiBase();

  return (
    <div
      className={cn(
        'mb-6 rounded-2xl border p-5',
        isAuthProblem
          ? 'bg-orange-500/10 border-orange-500/30'
          : 'bg-red-500/10 border-red-500/30'
      )}
    >
      <div className="flex items-start gap-4">
        <div
          className={cn(
            'p-2 rounded-xl shrink-0',
            isAuthProblem ? 'bg-orange-500/20' : 'bg-red-500/20'
          )}
        >
          {isAuthProblem ? (
            <ShieldAlert className="w-5 h-5 text-orange-400" />
          ) : (
            <PlugZap className="w-5 h-5 text-red-400" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <h3
            className={cn(
              'font-bold mb-1',
              isAuthProblem ? 'text-orange-400' : 'text-red-400'
            )}
          >
            {isAuthProblem ? 'Потрібен API-ключ' : 'Немає зв\'язку з ботом'}
          </h3>
          <p className="text-sm text-slate-300 leading-relaxed mb-4">
            {isAuthProblem ? (
              <>
                Бекенд відповідає 401. Введи значення <code className="text-orange-300">API_KEY</code> з
                файлу <code className="text-orange-300">.env</code> бота — воно збережеться лише в
                цьому браузері.
              </>
            ) : configProblem ? (
              configProblem
            ) : (
              <>
                Не достукались до <code className="text-red-300">{API_BASE_URL}</code>. Перевір, що
                бот запущений і що <code className="text-red-300">VITE_API_URL</code> вказує на
                правильну адресу.
              </>
            )}
          </p>

          <div className="flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1 min-w-0">
              <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
              <input
                type="password"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSave()}
                placeholder={getApiKey() ? 'Ключ збережено — введи новий, щоб замінити' : 'API_KEY'}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-10 pr-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all"
              />
            </div>
            <button
              onClick={handleSave}
              disabled={isChecking}
              className="px-6 py-2.5 bg-accent-500 hover:bg-accent-400 disabled:opacity-50 text-slate-950 text-sm font-bold rounded-xl transition-all shrink-0 flex items-center justify-center gap-2 focus:ring-2 focus:ring-accent-500/50 outline-none"
            >
              {isChecking ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {isChecking ? 'Перевіряю' : 'Підключитись'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
