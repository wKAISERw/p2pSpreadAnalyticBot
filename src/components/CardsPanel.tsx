import React, { useState } from 'react';
import useSWR from 'swr';
import { motion, AnimatePresence } from 'motion/react';
import {
  CreditCard, ArrowDownLeft, ArrowUpRight, Loader2, ChevronDown, Link2, Settings2,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { Card, CardReportRow, CardTransaction } from '../types';
import { CardSettings } from './cards/CardSettings';

const uah = (v: number) => `${Math.round(v ?? 0).toLocaleString('uk-UA')} ₴`;

/** Ліміти приходять із get_card_effective_limits — ключі можуть відрізнятись
 *  залежно від того, чи є в картки власні override-и. Дістаємо м'яко. */
const limitOf = (card: Card, ...keys: string[]): number | null => {
  for (const key of keys) {
    const v = card.limits?.[key];
    if (typeof v === 'number' && v > 0) return v;
  }
  return null;
};

/**
 * Картки користувача з /api/v1/cards: баланс, ліміти й вибірка за добу
 * та календарний місяць. Раніше це було доступно тільки в меню «Картки».
 */
export default function CardsPanel() {
  // Особа береться з підтвердженої сесії — раніше тут був ID,
  // введений руками в налаштуваннях, тобто будь-який.
  const telegramId = useAppStore(state => state.auth?.telegramId);

  const { data: cards, error, isLoading, mutate } = useSWR<Card[]>(
    telegramId ? ['/cards', telegramId] : null,
    () => api.getCards(telegramId!),
    { refreshInterval: 30000, shouldRetryOnError: false }
  );

  if (!telegramId) return <Empty text="Потрібен вхід через Telegram." />;
  if (isLoading) return <Empty text="Читаю картки…" spinner />;
  if (error) return <Empty text={`Не вдалось завантажити картки: ${(error as Error).message}`} />;
  if (!cards?.length) return <Empty text="Карток ще немає — додай їх у боті, меню «Картки»." />;

  const totalBalance = cards.reduce((sum, c) => sum + (c.balance ?? 0), 0);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Картки</h1>
          <p className="text-sm text-slate-400">{cards.length} шт · загальний баланс {uah(totalBalance)}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {cards.map(card => <CardItem key={card.id} card={card} onChanged={mutate} />)}
      </div>

      <CardsReport />
    </div>
  );
}

const CardItem: React.FC<{ card: Card; onChanged: () => void }> = ({ card, onChanged }) => {
  const [panel, setPanel] = useState<'none' | 'transactions' | 'settings'>('none');

  const dailyOut = limitOf(card, 'daily_out', 'dailyOut', 'day_out');
  const monthlyOut = limitOf(card, 'monthly_out', 'monthlyOut', 'month_out');
  const dailyIn = limitOf(card, 'daily_in', 'dailyIn', 'day_in');
  const monthlyIn = limitOf(card, 'monthly_in', 'monthlyIn', 'month_in');

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl overflow-hidden"
    >
      <div className="p-6">
        <div className="flex items-start justify-between mb-5">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-slate-800/60 rounded-xl">
              <CreditCard className="w-5 h-5 text-accent-400" />
            </div>
            <div>
              <div className="font-bold text-white">{card.label || 'Без назви'}</div>
              <div className="text-xs text-slate-400 capitalize">
                {card.bankName} · •••• {card.lastFour}
              </div>
            </div>
          </div>

          <div className="text-right">
            <div className="text-xl font-black text-white tabular-nums">{uah(card.balance)}</div>
            {card.status && (
              <div className="text-[10px] uppercase tracking-wider text-slate-500">{card.status}</div>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <UsageBlock
            icon={<ArrowUpRight className="w-3.5 h-3.5" />}
            label="Витрати"
            tone="text-red-400"
            day={card.usedDaily?.out ?? 0}
            month={card.usedMonthly?.out ?? 0}
            dayLimit={dailyOut}
            monthLimit={monthlyOut}
          />
          <UsageBlock
            icon={<ArrowDownLeft className="w-3.5 h-3.5" />}
            label="Надходження"
            tone="text-accent-400"
            day={card.usedDaily?.in ?? 0}
            month={card.usedMonthly?.in ?? 0}
            dayLimit={dailyIn}
            monthLimit={monthlyIn}
          />
        </div>
      </div>

      <div className="flex border-t border-slate-800/50">
        <button
          onClick={() => setPanel(panel === 'transactions' ? 'none' : 'transactions')}
          className="flex-1 px-6 py-3 flex items-center justify-center gap-2 text-xs font-bold text-slate-400 hover:text-white hover:bg-slate-800/30 transition-colors"
        >
          Транзакції
          <ChevronDown className={cn('w-4 h-4 transition-transform', panel === 'transactions' && 'rotate-180')} />
        </button>
        <div className="w-px bg-slate-800/50" />
        <button
          onClick={() => setPanel(panel === 'settings' ? 'none' : 'settings')}
          className="flex-1 px-6 py-3 flex items-center justify-center gap-2 text-xs font-bold text-slate-400 hover:text-white hover:bg-slate-800/30 transition-colors"
        >
          <Settings2 className="w-3.5 h-3.5" />
          Ліміти й сповіщення
        </button>
      </div>

      <AnimatePresence>
        {panel !== 'none' && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-slate-800/50"
          >
            {panel === 'transactions'
              ? <Transactions cardId={card.id} />
              : <CardSettings card={card} onSaved={onChanged} />}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function UsageBlock({
  icon, label, tone, day, month, dayLimit, monthLimit,
}: {
  icon: React.ReactNode;
  label: string;
  tone: string;
  day: number;
  month: number;
  dayLimit: number | null;
  monthLimit: number | null;
}) {
  return (
    <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
      <div className={cn('flex items-center gap-1.5 text-xs font-bold mb-3', tone)}>
        {icon}
        {label}
      </div>
      <UsageBar caption="Доба" used={day} limit={dayLimit} />
      <UsageBar caption="Місяць" used={month} limit={monthLimit} />
    </div>
  );
}

function UsageBar({ caption, used, limit }: { caption: string; used: number; limit: number | null }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;

  return (
    <div className="mb-2 last:mb-0">
      <div className="flex justify-between text-[11px] text-slate-400 mb-1 tabular-nums">
        <span>{caption}</span>
        <span>{limit ? `${uah(used)} / ${uah(limit)}` : uah(used)}</span>
      </div>
      {limit ? (
        <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all',
              pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-orange-500' : 'bg-accent-500'
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : (
        <div className="text-[10px] text-slate-600">ліміт не заданий</div>
      )}
    </div>
  );
}

function Transactions({ cardId }: { cardId: string }) {
  const { data: txs, isLoading } = useSWR<CardTransaction[]>(
    ['/cards/transactions', cardId],
    () => api.getCardTransactions(cardId, 30),
    { shouldRetryOnError: false }
  );

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-6 py-6 text-xs text-slate-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Завантажую…
      </div>
    );
  }
  if (!txs?.length) {
    return <div className="px-6 py-6 text-xs text-slate-500">Транзакцій ще немає.</div>;
  }

  return (
    <div className="max-h-72 overflow-y-auto divide-y divide-slate-800/50">
      {txs.map(tx => (
        <div key={tx.id} className="px-6 py-3 flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="text-xs text-slate-300 flex items-center gap-1.5">
              <span className="capitalize">{tx.type}</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-500">{tx.source}</span>
              {tx.linkedOrderId && (
                <span title={`Ордер ${tx.linkedOrderId}`} className="text-accent-500">
                  <Link2 className="w-3 h-3" />
                </span>
              )}
            </div>
            <div className="text-[11px] text-slate-500 font-mono tabular-nums">
              {new Date(tx.timestamp * 1000).toLocaleString('uk-UA')}
            </div>
          </div>

          <div
            className={cn(
              'text-sm font-bold tabular-nums shrink-0',
              tx.direction === 'in' ? 'text-accent-400' : 'text-red-400'
            )}
          >
            {tx.direction === 'in' ? '+' : '−'}{uah(tx.amount)}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Зведення по всіх картках: обіг і кількість транзакцій за період. */
function CardsReport() {
  const { data: report } = useSWR<CardReportRow[]>(
    '/cards/report',
    () => api.getCardsReport(),
    { shouldRetryOnError: false }
  );

  if (!report?.length) return null;

  // Набір метрик залежить від get_card_report_stats — не прибиваємо його
  // цвяхами, а показуємо те, що реально прийшло.
  const metricKeys = Array.from(
    new Set(
      report.flatMap(row =>
        Object.entries(row)
          .filter(([key, v]) => typeof v === 'number' && !BASE_FIELDS.has(key))
          .map(([key]) => key)
      )
    )
  );

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6 overflow-x-auto"
    >
      <h2 className="text-lg font-bold text-white mb-4">Звіт по картках</h2>

      <table className="w-full text-sm min-w-[38rem]">
        <thead>
          <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
            <th className="text-left font-medium py-2 pr-4">Картка</th>
            <th className="text-right font-medium py-2 pr-4">Баланс</th>
            {metricKeys.map(key => (
              <th key={key} className="text-right font-medium py-2 pr-4">{humanize(key)}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {report.map(row => (
            <tr key={row.cardId}>
              <td className="py-2.5 pr-4">
                <span className="font-bold text-white">{row.label || row.bankName}</span>
                <span className="text-slate-500 text-xs"> ·••••{row.lastFour}</span>
              </td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-slate-200">
                {uah(row.balance)}
              </td>
              {metricKeys.map(key => (
                <td key={key} className="py-2.5 pr-4 text-right tabular-nums text-slate-400">
                  {typeof row[key] === 'number' ? Math.round(row[key] as number).toLocaleString('uk-UA') : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </motion.section>
  );
}

const BASE_FIELDS = new Set(['balance']);

const humanize = (key: string) =>
  key.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase());

function Empty({ text, spinner }: { text: string; spinner?: boolean }) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-slate-400 text-sm">
      {spinner && <Loader2 className="w-4 h-4 animate-spin" />}
      {text}
    </div>
  );
}
