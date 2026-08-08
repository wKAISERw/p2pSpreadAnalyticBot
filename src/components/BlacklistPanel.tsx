import React, { useState } from 'react';
import { ShieldBan, Search, Trash2, Plus, Loader2, X, User, Globe } from 'lucide-react';
import { toast } from 'sonner';
import { BlacklistEntry, BlacklistScope } from '../types';
import { cn } from '../lib/utils';
import { motion, AnimatePresence } from 'motion/react';
import useSWR from 'swr';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { useExchanges } from '../hooks/useExchanges';

type Tab = 'all' | BlacklistScope;

export default function BlacklistPanel() {
  const [searchTerm, setSearchTerm] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [tab, setTab] = useState<Tab>('all');
  const isAdmin = useAppStore(state => state.auth?.isAdmin ?? false);
  const { data: blacklist = [], mutate } = useSWR('/blacklist', api.getBlacklist);

  const personalCount = blacklist.filter(e => e.scope === 'personal').length;
  const globalCount = blacklist.length - personalCount;

  const filteredList = blacklist
    .filter(entry => tab === 'all' || entry.scope === tab)
    .filter(entry =>
      entry.merchantName?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      entry.merchantId?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      entry.reason?.toLowerCase().includes(searchTerm.toLowerCase())
    );

  /** Спільний запис знімає лише адмін — свій може будь-хто. */
  const canEdit = (entry: BlacklistEntry) => entry.scope === 'personal' || isAdmin;

  const handleUnban = async (entry: BlacklistEntry) => {
    // Раніше тут перевірялось `if (success)` на тілі відповіді. Клієнт
    // кидає виняток на будь-яку помилку, тож гілка else була недосяжна, а
    // невдалий розбан лишався без жодного слова користувачу.
    try {
      await api.removeFromBlacklist(entry.merchantId, entry.exchange, entry.scope);
      await mutate();
      toast.success(`${entry.merchantName || entry.merchantId} розблоковано`);
    } catch (e: any) {
      toast.error(`Не вдалось розблокувати: ${e?.message ?? 'помилка'}`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-red-500/10 border border-red-500/20 rounded-3xl p-6 mb-8">
        <div className="flex items-start gap-4">
          <div className="p-2 bg-red-500/20 rounded-xl">
            <ShieldBan className="w-6 h-6 text-red-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-red-400 mb-1">Чорний список</h2>
            <p className="text-sm text-red-500/80 leading-relaxed">
              Мерчанти, яких сканер обходить стороною. Списків два:{' '}
              <b>твій</b> — свій у кожного, впливає лише на твої алерти й
              редагується без обмежень; <b>спільний</b> — його наповнюють
              ризик-движок і адміністратор, і він діє на всіх.
            </p>
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-[2rem] overflow-hidden">
        <div className="p-6 border-b border-slate-800 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-4 top-3 w-5 h-5 text-slate-500" />
              <input
                type="text"
                placeholder="Пошук за назвою, ID або причиною…"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-12 pr-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all"
              />
            </div>

            <div className="flex items-center gap-1 bg-slate-950 border border-slate-800 rounded-xl p-1 shrink-0">
              {([
                ['all', `Усі (${blacklist.length})`],
                ['personal', `Мої (${personalCount})`],
                ['global', `Спільні (${globalCount})`],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setTab(value)}
                  className={cn(
                    'px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors whitespace-nowrap',
                    tab === value ? 'bg-slate-800 text-white' : 'text-slate-500 hover:text-slate-300'
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Кнопка доступна всім: бан у власний список — не адмінська дія.
              Раніше вона взагалі нічого не робила (обробника не було). */}
          <motion.button
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => setIsAdding(v => !v)}
            className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all focus:ring-2 focus:ring-slate-500/50 outline-none shrink-0"
          >
            {isAdding ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
            {isAdding ? 'Скасувати' : 'Забанити вручну'}
          </motion.button>
        </div>

        <AnimatePresence>
          {isAdding && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden border-b border-slate-800 bg-slate-950/50"
            >
              <ManualBanForm
                isAdmin={isAdmin}
                onDone={async () => {
                  setIsAdding(false);
                  await mutate();
                }}
              />
            </motion.div>
          )}
        </AnimatePresence>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-950/50 border-b border-slate-800 text-xs uppercase tracking-widest text-slate-400">
                <th className="p-4 font-semibold">Мерчант</th>
                <th className="p-4 font-semibold">Біржа</th>
                <th className="p-4 font-semibold">Список</th>
                <th className="p-4 font-semibold">Причина</th>
                <th className="p-4 font-semibold">Джерело</th>
                <th className="p-4 font-semibold text-right">Дії</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {filteredList.length > 0 ? filteredList.map((entry) => (
                <tr
                  key={`${entry.scope}-${entry.exchange}-${entry.merchantId}`}
                  className="hover:bg-slate-800/20 transition-colors"
                >
                  <td className="p-4">
                    <div className="font-bold text-white">{entry.merchantName}</div>
                    <div className="text-xs font-mono text-slate-400 tabular-nums">{entry.merchantId}</div>
                  </td>
                  <td className="p-4">
                    <span className="px-2 py-1 bg-slate-800 text-slate-300 text-xs font-bold rounded uppercase tracking-wider">
                      {entry.exchange}
                    </span>
                  </td>
                  <td className="p-4">
                    <span
                      className={cn(
                        'inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11px] font-bold border',
                        entry.scope === 'personal'
                          ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                          : 'bg-slate-800 border-slate-700 text-slate-300'
                      )}
                      title={
                        entry.scope === 'personal'
                          ? 'Твій запис — впливає лише на твої алерти'
                          : 'Спільний запис — діє на всіх користувачів'
                      }
                    >
                      {entry.scope === 'personal'
                        ? <><User className="w-3 h-3" /> Мій</>
                        : <><Globe className="w-3 h-3" /> Спільний</>}
                    </span>
                  </td>
                  <td className="p-4">
                    <div className="text-sm text-red-400 max-w-xs truncate" title={entry.reason}>
                      {entry.reason}
                    </div>
                  </td>
                  <td className="p-4">
                    <div className="text-xs text-slate-400">{entry.source}</div>
                    <div className="text-xs text-slate-500 font-mono tabular-nums">
                      {entry.addedAt ? new Date(entry.addedAt * 1000).toLocaleDateString('uk-UA') : '—'}
                    </div>
                  </td>
                  <td className="p-4 text-right">
                    {canEdit(entry) ? (
                      <motion.button
                        whileHover={{ scale: 1.1 }}
                        whileTap={{ scale: 0.9 }}
                        onClick={() => handleUnban(entry)}
                        className="p-2 hover:bg-red-500/10 text-slate-500 hover:text-red-400 rounded-lg transition-colors focus:ring-2 focus:ring-red-500/50 outline-none"
                        title="Розблокувати мерчанта"
                      >
                        <Trash2 className="w-4 h-4" />
                      </motion.button>
                    ) : (
                      <span className="text-[11px] text-slate-600" title="Спільний список знімає адміністратор">
                        тільки адмін
                      </span>
                    )}
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-slate-400 text-sm">
                    {blacklist.length === 0
                      ? 'Чорний список порожній.'
                      : 'За цим запитом нічого не знайдено.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/**
 * Ручний бан. Дзеркалить `blacklist:add` у боті: там теж питають біржу,
 * ID мерчанта і причину, бо саме причина потім пояснює, чому зв'язку
 * відкинуто.
 */
function ManualBanForm({ isAdmin, onDone }: { isAdmin: boolean; onDone: () => Promise<void> }) {
  const { names: exchangeNames } = useExchanges();
  const [exchange, setExchange] = useState('');
  const [merchantId, setMerchantId] = useState('');
  const [merchantName, setMerchantName] = useState('');
  const [reason, setReason] = useState('');
  const [scope, setScope] = useState<BlacklistScope>('personal');
  const [saving, setSaving] = useState(false);

  const canSubmit = Boolean(exchange && merchantId.trim() && reason.trim());

  const submit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    try {
      await api.addToBlacklist(
        {
          exchange,
          merchantId: merchantId.trim(),
          merchantName: merchantName.trim() || merchantId.trim(),
          reason: reason.trim(),
          source: 'web',
        },
        scope
      );
      toast.success(
        scope === 'personal'
          ? `${merchantId.trim()} додано в твій список`
          : `${merchantId.trim()} заблоковано для всіх`
      );
      setMerchantId('');
      setMerchantName('');
      setReason('');
      await onDone();
    } catch (e: any) {
      toast.error(`Не додано: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 space-y-4">
      {/* Вибір списку є тільки в адміна: решті писати нікуди, крім свого. */}
      {isAdmin && (
        <div>
          <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-2">
            Куди додати
          </div>
          <div className="flex flex-wrap gap-2">
            {([
              ['personal', 'Тільки мені', 'Впливає лише на твої алерти'],
              ['global', 'Усім користувачам', 'Спільний список — діє на всіх'],
            ] as const).map(([value, label, hint]) => (
              <button
                key={value}
                onClick={() => setScope(value)}
                title={hint}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
                  scope === value
                    ? 'bg-red-500/10 border-red-500/30 text-red-400'
                    : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-2">Біржа</div>
        <div className="flex flex-wrap gap-2">
          {exchangeNames.map(name => (
            <button
              key={name}
              onClick={() => setExchange(name)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
                exchange === name
                  ? 'bg-red-500/10 border-red-500/30 text-red-400'
                  : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
              )}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Field label="ID мерчанта" hint="Обов'язково — саме за ним звіряється сканер">
          <input
            value={merchantId}
            onChange={e => setMerchantId(e.target.value)}
            placeholder="напр. 1a2b3c4d"
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-red-500 outline-none transition-all font-mono"
          />
        </Field>
        <Field label="Назва" hint="Для очей — якщо порожньо, підставимо ID">
          <input
            value={merchantName}
            onChange={e => setMerchantName(e.target.value)}
            placeholder="нік мерчанта"
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-red-500 outline-none transition-all"
          />
        </Field>
      </div>

      <Field label="Причина" hint="Видно в алертах і в цій таблиці">
        <input
          value={reason}
          onChange={e => setReason(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          placeholder="напр. тягне в ТГ / треті особи / скам з чеком"
          className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-red-500 outline-none transition-all"
        />
      </Field>

      <button
        onClick={submit}
        disabled={!canSubmit || saving}
        className="flex items-center gap-2 px-6 py-2.5 bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 disabled:opacity-40 text-xs font-bold rounded-xl transition-all"
      >
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldBan className="w-4 h-4" />}
        Заблокувати
      </button>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-1">{label}</div>
      <div className="text-[11px] text-slate-500 mb-2">{hint}</div>
      {children}
    </div>
  );
}
