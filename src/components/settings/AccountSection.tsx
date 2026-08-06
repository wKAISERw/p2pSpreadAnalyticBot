import React, { useState } from 'react';
import { motion } from 'motion/react';
import { UserCheck, Link2, Unlink, ShieldCheck, Loader2, Info } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { authApi } from '../../services/api';
import { useAppStore } from '../../store';
import { signInWithGoogle as googlePopup } from '../../lib/google';

/**
 * Акаунт і способи входу.
 *
 * Telegram тут не «спосіб входу», а сама особа: тільки він пов'язує сесію
 * з користувачем бота. Google прив'язується до вже підтвердженого
 * Telegram-акаунта і далі працює як швидкий вхід.
 *
 * Поля «введи свій Telegram ID», яке тут було раніше, більше немає — воно
 * ніяк не перевірялось, тож будь-хто міг вписати чужий і читати чужі дані.
 */
export default function AccountSection() {
  const auth = useAppStore(state => state.auth);
  const setIdentities = useAppStore(state => state.setIdentities);
  const [busy, setBusy] = useState(false);

  if (!auth) return null;

  const google = auth.identities?.find(i => i.provider === 'google');

  const link = async () => {
    setBusy(true);
    try {
      const profile = await googlePopup();
      const result = await authApi.linkGoogle(profile.uid, profile.email);
      setIdentities(result.identities);
      toast.success('Google прив\'язано');
    } catch (e: any) {
      toast.error(e?.message ?? 'Не вдалось прив\'язати');
    } finally {
      setBusy(false);
    }
  };

  const unlink = async () => {
    setBusy(true);
    try {
      await authApi.unlinkGoogle();
      setIdentities(auth.identities.filter(i => i.provider !== 'google'));
      toast.success('Google відв\'язано');
    } catch (e: any) {
      toast.error(e?.message ?? 'Не вдалось відв\'язати');
    } finally {
      setBusy(false);
    }
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex items-center gap-3 mb-5">
        <div className="p-2 bg-slate-800/60 rounded-xl">
          <UserCheck className="w-5 h-5 text-accent-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-white">Акаунт</h2>
          <p className="text-xs text-slate-400">Способи входу в дашборд</p>
        </div>
      </div>

      <div className="space-y-3">
        {/* Telegram — основна особа, відв'язати не можна */}
        <div className="flex items-center justify-between px-4 py-3.5 bg-slate-950/50 border border-accent-500/20 rounded-2xl">
          <div className="flex items-center gap-3 min-w-0">
            <ShieldCheck className="w-5 h-5 text-accent-400 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-bold text-white">Telegram</div>
              <div className="text-xs text-slate-400 tabular-nums">
                ID {auth.telegramId}
                {auth.isAdmin && <span className="ml-2 text-orange-400">· адміністратор</span>}
              </div>
            </div>
          </div>
          <span className="text-[10px] uppercase tracking-wider font-bold text-accent-400 shrink-0">
            основний
          </span>
        </div>

        {/* Google — необов'язкова прив'язка */}
        <div className="flex items-center justify-between px-4 py-3.5 bg-slate-950/50 border border-slate-800/50 rounded-2xl gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <Link2 className={cn('w-5 h-5 shrink-0', google ? 'text-blue-400' : 'text-slate-600')} />
            <div className="min-w-0">
              <div className="text-sm font-bold text-white">Google</div>
              <div className="text-xs text-slate-400 truncate">
                {google ? (google.email || 'прив\'язано') : 'не прив\'язано'}
              </div>
            </div>
          </div>

          <button
            onClick={google ? unlink : link}
            disabled={busy}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold border transition-colors shrink-0 disabled:opacity-50',
              google
                ? 'bg-slate-800 border-slate-700 text-slate-300 hover:text-red-400 hover:border-red-500/30'
                : 'bg-blue-500/10 border-blue-500/30 text-blue-400 hover:bg-blue-500/20'
            )}
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : google ? <Unlink className="w-3.5 h-3.5" /> : <Link2 className="w-3.5 h-3.5" />}
            {google ? 'Відв\'язати' : 'Прив\'язати'}
          </button>
        </div>
      </div>

      <div className="mt-5 p-4 bg-slate-950/50 border border-slate-800/50 rounded-2xl">
        <div className="flex items-center gap-2 mb-2">
          <Info className="w-4 h-4 text-slate-400 shrink-0" />
          <span className="text-xs font-bold text-white">Що дає прив'язка Google</span>
        </div>
        <ul className="text-[11px] text-slate-400 space-y-1 leading-snug">
          <li>· Вхід на новому пристрої без походу в бот по код</li>
          <li>· Налаштування сайту (звук, тема, ціль, режим, приховані біржі)
            переїжджають між браузерами</li>
          <li>· Запасний доступ, якщо Telegram недоступний</li>
        </ul>
        <p className="text-[11px] text-slate-500 mt-3 leading-snug">
          Усе, що стосується грошей — фільтри, картки, ключі бірж — лишається
          прив'язаним до Telegram. Google цього не бачить і не змінює.
        </p>
      </div>
    </motion.section>
  );
}
