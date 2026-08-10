import React, { useState } from 'react';
import useSWR from 'swr';
import { CreditCard, ChevronDown, ArrowRight, Flame } from 'lucide-react';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { useMyBankBalances } from './BankChips';
import { BankRef, CardMatch } from '../types';
import { cn } from '../lib/utils';

const uah = (v: number) => `${Math.round(v).toLocaleString('uk-UA')} ₴`;

/**
 * Які картки підходять під цю угоду — те саме, що бот шле окремим
 * повідомленням після алерта («💳 Рекомендований пластик під угоду»).
 *
 * На сайті цього блоку не було взагалі: людина бачила ордер, але дізнатись,
 * чи зможе його взяти, могла лише відкривши Telegram. Найцінніше тут не
 * «підходить/не підходить», а що з цим робити — тому поради переказів
 * стоять поруч із причиною, а не в окремому місці.
 *
 * Згорнутий за замовчуванням: коли картка підходить, деталі не потрібні.
 */
export function CardMatchBlock({
  banks, amount, direction,
}: {
  /** Усі банки, які приймає мерчант. */
  banks: BankRef[];
  amount: number;
  direction: 'buy' | 'sell';
}) {
  const [open, setOpen] = useState(false);
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const balances = useMyBankBalances();

  // Банк вибирається з ПЕРЕТИНУ, і серед нього — той, де грошей найбільше.
  //
  // Два кроки, обидва потрібні. Перший `banks[0]` брав банк, якого в нас
  // могло не бути взагалі («немає активних карток» при повних картках у
  // сусідньому рядку списку). Другий: навіть у перетині порядок задає
  // мерчант, і ПУМБ із 5 000 ₴ ставав відповіддю «не вистачає» там, де в
  // Monobank того ж списку лежить 21 000 ₴.
  //
  // Це не заміна движку: він усередині банку сам збере суму з кількох
  // карток. Тут лише вибір, ПРО ЯКИЙ банк його питати.
  const usable = banks
    .filter(b => b.known && balances.has(b.slug))
    .sort((a, b) => (balances.get(b.slug) ?? 0) - (balances.get(a.slug) ?? 0));
  const bank = usable[0]?.slug ?? '';

  const { data } = useSWR<CardMatch>(
    telegramId && bank && amount > 0 ? ['/cards/match', bank, amount, direction] : null,
    () => api.getCardMatch(bank, amount, direction),
    { revalidateOnFocus: false, shouldRetryOnError: false, dedupingInterval: 15000 }
  );

  // Жодного спільного банку — це інша відповідь, ніж «картки не пройшли
  // за лімітами», і плутати їх не можна: тут не поповнення потрібне, а
  // картка іншого банку.
  if (banks.length > 0 && usable.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-800/70 bg-slate-950/50 px-3 py-2">
        <div className="flex items-center gap-2">
          <CreditCard className="w-3.5 h-3.5 text-slate-500 shrink-0" />
          <span className="text-[11px] text-slate-400">
            Немає картки жодного з банків мерчанта
          </span>
        </div>
      </div>
    );
  }

  if (!data || data.status === 'disabled') return null;

  const ok = data.status === 'success';
  const needsSplit = data.status === 'needs_split';
  const best = data.bestCard as any;

  // Головний рядок відповідає на єдине питання, яке тут стоїть: беремо чи ні.
  const headline = ok && best
    ? `${best.bank_name ?? data.bankName} *${best.last_four ?? ''}`
    : needsSplit
      ? 'Потрібен спліт між картками'
      : data.availableUah > 0
        ? `Не вистачає ${uah(Math.max(0, amount - data.availableUah))}`
        : 'Немає придатної картки';

  return (
    <div className={cn(
      'rounded-2xl border overflow-hidden',
      ok ? 'bg-accent-500/5 border-accent-500/20' : 'bg-slate-950/50 border-slate-800/70'
    )}>
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-800/20 transition-colors"
      >
        <CreditCard className={cn(
          'w-3.5 h-3.5 shrink-0',
          ok ? 'text-accent-400' : 'text-slate-500'
        )} />
        <span className="text-[11px] font-bold text-slate-300">
          {direction === 'buy' ? 'Платимо з' : 'Приймаємо на'}:
        </span>
        <span className={cn(
          'text-[11px] truncate',
          ok ? 'text-accent-400 font-bold' : 'text-slate-400'
        )}>
          {headline}
        </span>
        <ChevronDown className={cn(
          'w-3.5 h-3.5 text-slate-600 ml-auto shrink-0 transition-transform',
          open && 'rotate-180'
        )} />
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-3">
          {/* Чому не підходить. Коди ті самі, що в статистиці відмов, тож
              «не вистачає балансу» тут і в дайджесті — одне й те саме. */}
          {data.rejections.length > 0 && (
            <div className="space-y-1">
              {data.rejections.map((r, i) => (
                <div key={i} className="text-[11px] text-slate-400 leading-snug">
                  {r.lastFour && (
                    <span className="text-slate-500">*{r.lastFour} — </span>
                  )}
                  {r.reason}
                </div>
              ))}
            </div>
          )}

          {/* Поради переказів — єдине, що людина може зробити просто зараз. */}
          {data.transferTips.length > 0 && (
            <div className="rounded-xl bg-slate-900/60 border border-slate-800 p-2.5 space-y-1.5">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">
                Щоб вистачило
              </div>
              {data.transferTips.map((t, i) => (
                <div key={i} className="flex items-center gap-1.5 text-[11px] text-slate-300">
                  <span className="font-bold tabular-nums text-accent-400">
                    {uah(t.amountUah)}
                  </span>
                  <span className="text-slate-500">
                    {t.fromBank} *{t.fromLastFour}
                  </span>
                  <ArrowRight className="w-3 h-3 text-slate-600 shrink-0" />
                  <span className="text-slate-500">
                    {t.toBank} *{t.toLastFour}
                  </span>
                </div>
              ))}
            </div>
          )}

          {data.balances.length > 0 && (
            <div className="space-y-1">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-600">
                Баланси карток
              </div>
              {data.balances.map(c => (
                <div key={c.id} className="flex items-center gap-1.5 text-[11px]">
                  <span className="text-slate-400">
                    {c.bankName} *{c.lastFour}
                  </span>
                  {c.isWarmedUp && (
                    <Flame className="w-2.5 h-2.5 text-orange-400 shrink-0" />
                  )}
                  <span className="ml-auto tabular-nums text-slate-300">
                    {uah(c.balance)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
