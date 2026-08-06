import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import { Send, KeyRound, Loader2, Info } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { authApi } from '../services/api';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../store';
import { AuthConfig, TelegramWidgetPayload } from '../types';
import { signInWithGoogle as googlePopup } from '../lib/google';

declare global {
  interface Window {
    onTelegramAuth?: (user: TelegramWidgetPayload) => void;
  }
}

/**
 * Вхід у дашборд.
 *
 * Telegram — основний спосіб: тільки він доводить, який ти користувач у боті.
 * Раніше telegram_id вбивався руками в налаштуваннях, тобто будь-хто міг
 * вказати чужий і читати чужі картки.
 *
 * Google — не окрема особа, а лише швидкий вхід для вже прив'язаного
 * акаунта: сам по собі Google-профіль не вказує на жоден Telegram.
 */
export default function LoginScreen() {
  const navigate = useNavigate();
  const storeAuth = useAppStore(state => state.setAuth);

  // Після успішного входу одразу ведемо в дашборд — лишати людину на
  // екрані логіну після вдалого логіну немає сенсу.
  const setAuth = (session: Parameters<typeof storeAuth>[0]) => {
    storeAuth(session);
    if (session) navigate('/app');
  };
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState<'code' | 'google' | null>(null);

  useEffect(() => {
    authApi.getConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  const submitCode = async () => {
    if (code.trim().length < 4) return;
    setBusy('code');
    try {
      setAuth(await authApi.loginWithCode(code.trim()));
      toast.success('Вхід виконано');
    } catch (e: any) {
      toast.error(e?.message ?? 'Код не підійшов');
    } finally {
      setBusy(null);
    }
  };

  const signInWithGoogle = async () => {
    setBusy('google');
    try {
      const profile = await googlePopup();
      setAuth(await authApi.loginWithGoogle(profile.uid));
      toast.success('Вхід виконано');
    } catch (e: any) {
      // 404 означає «Google не прив'язаний» — це не помилка, а підказка.
      toast.error(e?.message ?? 'Не вдалось увійти через Google');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-[85vh] py-12 px-4">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md"
      >
        <div className="text-center mb-8">
          <div className="w-16 h-16 bg-accent-500/20 border border-accent-500/30 rounded-2xl flex items-center justify-center mx-auto mb-5 shadow-lg shadow-accent-500/10">
            <div className="w-8 h-8 bg-accent-500 rounded-xl" />
          </div>
          <h1 className="text-2xl font-bold text-white mb-2">
            ARBIX <span className="text-accent-400">QUANTUM</span>
          </h1>
          <p className="text-sm text-slate-400 max-w-xs mx-auto">
            Увійди тим самим Telegram, у якому користуєшся ботом — фільтри,
            картки й баланси підтягнуться самі.
          </p>
        </div>

        <div className="bg-slate-900/80 border border-slate-800/80 backdrop-blur-2xl rounded-3xl p-6 sm:p-8 space-y-6 shadow-2xl shadow-slate-950/80">
          {config?.widgetAvailable && (
            <div>
              <SectionLabel icon={<Send className="w-4 h-4 text-blue-400" />} text="Вхід одним кліком" />
              <TelegramWidget botUsername={config.botUsername} onAuth={setAuth} />
            </div>
          )}

          <div>
            <SectionLabel
              icon={<KeyRound className="w-4 h-4 text-accent-400" />}
              text="Код із бота"
            />
            <p className="text-xs text-slate-400 mb-4 leading-relaxed">
              Надішли боту <code className="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-accent-400 font-mono">/login</code> — він відповість
              шестизначним кодом. Код живе 5 хвилин і спрацьовує один раз.
            </p>

            <div className="flex gap-2.5">
              <input
                value={code}
                onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                onKeyDown={e => e.key === 'Enter' && submitCode()}
                placeholder="000000"
                inputMode="numeric"
                className="flex-1 min-w-0 bg-slate-950/90 border border-slate-700/80 rounded-xl px-4 py-3 text-center text-lg sm:text-xl font-bold tracking-[0.3em] text-white placeholder-slate-600 focus:border-accent-500 focus:ring-2 focus:ring-accent-500/40 outline-none transition-all"
              />
              <button
                onClick={submitCode}
                disabled={busy !== null || code.length < 4}
                className="shrink-0 px-5 sm:px-6 py-3 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-sm font-bold rounded-xl transition-all shadow-lg shadow-accent-500/20 flex items-center gap-2"
              >
                {busy === 'code' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                Увійти
              </button>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-slate-800" />
            <span className="text-[10px] uppercase tracking-widest text-slate-500 font-semibold">або</span>
            <div className="flex-1 h-px bg-slate-800" />
          </div>

          <div>
            <button
              onClick={signInWithGoogle}
              disabled={busy !== null}
              className="w-full px-6 py-3 bg-slate-800/90 hover:bg-slate-700/90 border border-slate-700/60 disabled:opacity-40 text-white text-sm font-bold rounded-xl transition-all flex items-center justify-center gap-2 shadow-md"
            >
              {busy === 'google' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Продовжити з Google
            </button>
            <div className="flex items-start gap-2 mt-3 text-[11px] text-slate-500 leading-snug">
              <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                Спрацює, лише якщо цей Google уже прив'язаний до Telegram-акаунта.
                Прив'язка робиться в налаштуваннях після входу через Telegram.
              </span>
            </div>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

function SectionLabel({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      {icon}
      <span className="text-xs font-bold uppercase tracking-wider text-slate-400">{text}</span>
    </div>
  );
}

/**
 * Офіційний скрипт віджета Telegram. Він сам малює кнопку в контейнер і
 * викликає глобальний колбек — іншого способу його підключити немає.
 * Домен сайту має бути прописаний у BotFather (/setdomain), інакше кнопка
 * просто не з'явиться; на цей випадок поруч завжди є вхід кодом.
 */
function TelegramWidget({
  botUsername,
  onAuth,
}: {
  botUsername: string;
  onAuth: (session: any) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!containerRef.current || !botUsername) return;

    window.onTelegramAuth = async (user: TelegramWidgetPayload) => {
      try {
        onAuth(await authApi.loginWithWidget(user));
        toast.success('Вхід виконано');
      } catch (e: any) {
        toast.error(e?.message ?? 'Telegram не підтвердив вхід');
      }
    };

    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.async = true;
    script.setAttribute('data-telegram-login', botUsername);
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '12');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    script.onerror = () => setFailed(true);

    containerRef.current.appendChild(script);
    const container = containerRef.current;

    return () => {
      container.innerHTML = '';
      delete window.onTelegramAuth;
    };
  }, [botUsername, onAuth]);

  return (
    <div>
      <div ref={containerRef} className={cn('flex justify-center', failed && 'hidden')} />
      {failed && (
        <p className="text-xs text-slate-500">
          Віджет не завантажився — скористайся входом за кодом нижче.
        </p>
      )}
    </div>
  );
}
