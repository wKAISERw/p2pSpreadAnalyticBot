import React from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { Users, Loader2, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { AdminUser } from '../types';

/**
 * Керування підписниками. Доступ перевіряє бекенд (require_admin) — тут
 * лише не малюємо зайвого; ховати кнопку самою по собі недостатньо.
 */
export default function AdminUsersPanel() {
  const isAdmin = useAppStore(state => state.auth?.isAdmin);
  const myId = useAppStore(state => state.auth?.telegramId);

  const { data: users, error, isLoading, mutate } = useSWR<AdminUser[]>(
    isAdmin ? '/admin/users' : null,
    () => api.getUsers(),
    { shouldRetryOnError: false }
  );

  if (!isAdmin) {
    return (
      <Empty icon={<ShieldAlert className="w-5 h-5 text-orange-400" />}>
        Розділ доступний лише адміністратору.
      </Empty>
    );
  }
  if (isLoading) return <Empty spinner>Читаю список користувачів…</Empty>;
  if (error) return <Empty>Не вдалось завантажити: {(error as Error).message}</Empty>;
  if (!users?.length) return <Empty>Підписників ще немає.</Empty>;

  const toggle = async (
    user: AdminUser,
    field: 'isActive' | 'isAlertsActive',
    next: boolean
  ) => {
    try {
      await api.updateUserState(user.userId, { [field]: next });
      await mutate();
      toast.success(`${user.userId}: ${next ? 'увімкнено' : 'вимкнено'}`);
    } catch (e: any) {
      toast.error(e?.message ?? 'Не вдалось оновити');
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Користувачі</h1>
        <p className="text-sm text-slate-400">{users.length} підписників сканера</p>
      </div>

      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6 overflow-x-auto"
      >
        <table className="w-full text-sm min-w-[46rem]">
          <thead>
            <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
              <th className="text-left font-medium py-2 pr-4">ID</th>
              <th className="text-left font-medium py-2 pr-4">Режим</th>
              <th className="text-right font-medium py-2 pr-4">Капітал</th>
              <th className="text-right font-medium py-2 pr-4">Мін. спред</th>
              <th className="text-center font-medium py-2 pr-4">Активний</th>
              <th className="text-center font-medium py-2">Алерти</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/50">
            {users.map(user => (
              <tr key={user.userId} className={cn(user.userId === myId && 'bg-accent-500/5')}>
                <td className="py-3 pr-4 font-mono text-xs text-slate-300">
                  {user.userId}
                  {user.userId === myId && (
                    <span className="ml-2 text-[10px] uppercase tracking-wider text-accent-400">
                      це ти
                    </span>
                  )}
                </td>
                <td className="py-3 pr-4">
                  <span className="px-2 py-0.5 rounded-md bg-slate-800 text-slate-300 text-[11px] font-bold">
                    {user.scannerMode}
                  </span>
                </td>
                <td className="py-3 pr-4 text-right tabular-nums text-slate-300">
                  {Math.round(user.workingCapital ?? 0).toLocaleString('uk-UA')} ₴
                </td>
                <td className="py-3 pr-4 text-right tabular-nums text-slate-300">
                  {(user.minSpreadPct ?? 0).toFixed(2)}%
                </td>
                <td className="py-3 pr-4 text-center">
                  <Toggle
                    checked={Boolean(user.isActive)}
                    // Бекенд і так не дасть деактивувати себе — не малюємо
                    // кнопку, яка гарантовано поверне 400.
                    disabled={user.userId === myId}
                    onChange={v => toggle(user, 'isActive', v)}
                  />
                </td>
                <td className="py-3 text-center">
                  <Toggle
                    checked={Boolean(user.isAlertsActive)}
                    onChange={v => toggle(user, 'isAlertsActive', v)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </motion.section>
    </div>
  );
}

function Toggle({
  checked, onChange, disabled,
}: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      disabled={disabled}
      title={disabled ? 'Власний акаунт деактивувати не можна' : undefined}
      className={cn(
        'w-10 h-5 rounded-full relative transition-colors inline-block disabled:opacity-30',
        checked ? 'bg-accent-500' : 'bg-slate-700'
      )}
    >
      <div className={cn(
        'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all',
        checked ? 'right-0.5' : 'left-0.5'
      )} />
    </button>
  );
}

function Empty({
  children, spinner, icon,
}: {
  children: React.ReactNode; spinner?: boolean; icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-slate-400 text-sm">
      {spinner && <Loader2 className="w-4 h-4 animate-spin" />}
      {icon}
      {children}
    </div>
  );
}
