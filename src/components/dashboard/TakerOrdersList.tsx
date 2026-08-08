import React from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import {
  ExternalLink, Copy, ShieldAlert, BadgeCheck, Clock, Gift, TrendingDown, TrendingUp,
  Smartphone,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { useAppStore } from '../../store';
import { TakerOrder, TakerOrdersResponse } from '../../types';
import { resolveOrderCardFields } from '../../lib/orderCard';
import { isMobileDevice } from '../../lib/device';

/**
 * Ордери, які проходять тейкер-фільтри — те саме, що бот шле в режимах
 * TAKER_BUY / TAKER_SELL.
 *
 * До появи GET /taker/orders цього на сайті не існувало взагалі: дашборд
 * умів показувати лише спред-зв'язки, тож у тейкер-режимах він виглядав
 * порожнім, поки в Telegram сипались ордери.
 *
 * Дедупу тут немає навмисно. У чаті він потрібен, щоб не надсилати те саме
 * двічі; на екрані «вже надіслане» — це рівно те, що треба бачити.
 */

const BANK_NAMES: Record<string, string> = {
  '43': 'Monobank', '14': 'PrivatBank', '64': 'ПУМБ', '48': 'А-Банк',
  '99': 'Ощадбанк', '380': 'Raiffeisen', '328': 'Sense', '319': 'OTP',
  '553': 'izibank', 'transfer': 'Global Transfer',
};

const uah = (v: number) => `${v.toFixed(2)} ₴`;

export function TakerOrdersList({ side }: { side: 'buy' | 'sell' | 'both' }) {
  const telegramId = useAppStore(state => state.auth?.telegramId);

  const { data, error, isLoading } = useSWR<TakerOrdersResponse>(
    telegramId ? ['/taker/orders', side] : null,
    () => api.getTakerOrders(side),
    { refreshInterval: 10000, shouldRetryOnError: false }
  );

  if (!telegramId) {
    return <Empty>Потрібен вхід через Telegram.</Empty>;
  }
  if (error) {
    return <Empty>Не вдалось завантажити ордери: {(error as Error).message}</Empty>;
  }
  if (isLoading && !data) {
    return <Empty>Рахую ордери за твоїми фільтрами…</Empty>;
  }
  if (data && !data.scanned) {
    return <Empty>Сканер ще не завершив жодного циклу — зачекай кілька секунд.</Empty>;
  }

  if (side === 'both') {
    return (
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Column title="Купівля" hint="ти купуєш USDT" side="buy" orders={data?.buy ?? []} />
        <Column title="Продаж" hint="ти продаєш USDT" side="sell" orders={data?.sell ?? []} />
      </div>
    );
  }

  const orders = side === 'buy' ? data?.buy ?? [] : data?.sell ?? [];
  return (
    <Column
      title={side === 'buy' ? 'Купівля' : 'Продаж'}
      hint={side === 'buy' ? 'ти купуєш USDT' : 'ти продаєш USDT'}
      side={side}
      orders={orders}
    />
  );
}

function Column({
  title, hint, side, orders,
}: {
  title: string;
  hint: string;
  side: 'buy' | 'sell';
  orders: TakerOrder[];
}) {
  const Icon = side === 'buy' ? TrendingDown : TrendingUp;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <Icon className={cn('w-4 h-4', side === 'buy' ? 'text-blue-400' : 'text-accent-400')} />
        <h2 className="text-sm font-bold text-white">{title}</h2>
        <span className="text-[11px] text-slate-500">{hint}</span>
        <span className="ml-auto text-[11px] text-slate-500 tabular-nums">
          {orders.length} шт
        </span>
      </div>

      {orders.length === 0 ? (
        <Empty>
          Нічого не проходить твої фільтри. Послаб пороги в «Фільтрах» —
          або зачекай наступного циклу.
        </Empty>
      ) : (
        <div className="space-y-3">
          {orders.map(order => (
            <OrderCard key={`${order.exchange}-${order.id}`} order={order} side={side} />
          ))}
        </div>
      )}
    </div>
  );
}

const OrderCardBase: React.FC<{ order: TakerOrder; side: 'buy' | 'sell' }> = ({ order, side }) => {
  // Набір полів картки налаштовується — див. lib/orderCard.ts. Без цього
  // картка або тонула в деталях, або ховала те, що комусь потрібне.
  //
  // Підписка тут, а не в батька: React.memo нижче блокує ререндер від
  // списку, але власна підписка на стор працює незалежно, тож перемикач
  // у налаштуваннях застосується одразу до всіх карток.
  const savedFields = useAppStore(state => state.userSettings.orderCard);
  const fields = resolveOrderCardFields(savedFields);

  const isRisky = Boolean(order.riskFlag) || order.compositeScore >= 50;
  const banks = (order.bankCodes ?? []).map(code => BANK_NAMES[code] ?? code);

  const copy = (value: string, what: string) => {
    navigator.clipboard.writeText(value);
    toast.success(`${what} скопійовано`);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        'bg-slate-900/60 border rounded-2xl p-4 transition-colors',
        isRisky ? 'border-orange-500/25' : 'border-slate-800/60'
      )}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="font-bold text-white truncate">{order.merchantName}</span>
            {fields.verified && order.isVerified && (
              <BadgeCheck className="w-3.5 h-3.5 text-blue-400 shrink-0" titleAccess="Верифікований" />
            )}
            {fields.subsidy && order.isNewUserSubsidy && (
              <Gift className="w-3.5 h-3.5 text-pink-400 shrink-0" titleAccess="Субсидія новачка" />
            )}
          </div>
          <div className="text-[11px] text-slate-500">
            {order.exchange}
            {fields.stats && (
              <> · {order.monthOrderCount} угод · {order.finishRatePct.toFixed(1)}%</>
            )}
          </div>
        </div>

        <div className="text-right shrink-0">
          <div className={cn(
            'text-lg font-black tabular-nums',
            side === 'buy' ? 'text-blue-400' : 'text-accent-400'
          )}>
            {uah(order.price)}
          </div>
          <div className="text-[11px] text-slate-500 tabular-nums">
            {Math.round(order.minLimit).toLocaleString('uk-UA')}–
            {Math.round(order.maxLimit).toLocaleString('uk-UA')} ₴
          </div>
        </div>
      </div>

      {fields.banks && banks.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {banks.map(bank => (
            <span
              key={bank}
              className="px-2 py-0.5 rounded-md bg-slate-950 border border-slate-800 text-[10px] font-bold text-slate-400"
            >
              {bank}
            </span>
          ))}
        </div>
      )}

      {(fields.volume || fields.age || fields.online || fields.reviews) && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
          {fields.volume && (
            <Metric label="Доступно" value={`${Math.round(order.availableAmount)} ₮`} />
          )}
          {fields.age && (
            <Metric
              label="Вік акаунта"
              value={order.accountAgeDays ? `${order.accountAgeDays} дн` : '—'}
            />
          )}
          {fields.online && (
            <Metric
              label="Онлайн"
              value={
                order.lastOnlineMins === null ? '—'
                  : order.lastOnlineMins === 0 ? 'зараз'
                  : `${order.lastOnlineMins} хв тому`
              }
            />
          )}
          {fields.reviews && (
            <Metric
              label="Негатив"
              value={order.reviewNegPct ? `${order.reviewNegPct.toFixed(1)}%` : '—'}
              tone={order.reviewNegPct >= 10 ? 'bad' : undefined}
            />
          )}
        </div>
      )}

      {fields.risk && isRisky && (
        <div className="flex items-start gap-1.5 mb-3 px-3 py-2 rounded-xl bg-orange-500/10 border border-orange-500/20">
          <ShieldAlert className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" />
          <span className="text-[11px] text-orange-300 leading-snug">
            {order.riskFlag || `Ризик-скор ${order.compositeScore}`}
          </span>
        </div>
      )}

      {fields.terms && order.tradeTerms && (
        <p className="text-[11px] text-slate-500 leading-snug mb-3 line-clamp-3">
          {order.tradeTerms}
        </p>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {/* На телефоні ведемо просто в застосунок біржі: там уже є жива
            сесія, тоді як мобільний браузер вимагатиме логінитись знову.
            Ці маршрути підтверджені прогоном на пристрої — див.
            bot/deeplinks.py. На десктопі кнопки немає: вона привела б на
            сторінку завантаження мобільного додатка. */}
        {order.appLink && isMobileDevice() && (
          <a
            href={order.appLink}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-500 hover:bg-accent-400 text-slate-950 text-[11px] font-bold transition-colors"
          >
            <Smartphone className="w-3 h-3" />
            У застосунку
          </a>
        )}
        {order.link && (
          <a
            href={order.link}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-bold transition-colors"
          >
            <ExternalLink className="w-3 h-3" />
            Відкрити
          </a>
        )}
        <button
          onClick={() => copy(order.price.toFixed(2), 'Ціну')}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-slate-400 hover:text-white text-[11px] font-bold transition-colors"
        >
          <Copy className="w-3 h-3" />
          Ціна
        </button>
        {fields.merchantId && (
          <button
            onClick={() => copy(order.merchantId, 'ID мерчанта')}
            title={order.merchantId}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-slate-400 hover:text-white text-[11px] font-mono transition-colors"
          >
            <Copy className="w-3 h-3" />
            ID
          </button>
        )}
      </div>
    </motion.div>
  );
};

/**
 * Список оновлюється раз на 10 секунд і віддає новий масив щоразу. Без
 * memo перемальовувались би всі картки, навіть якщо змінилась одна ціна.
 */
const OrderCard = React.memo(
  OrderCardBase,
  (prev, next) =>
    prev.order.id === next.order.id &&
    prev.order.price === next.order.price &&
    prev.order.availableAmount === next.order.availableAmount &&
    prev.order.riskFlag === next.order.riskFlag &&
    prev.order.lastOnlineMins === next.order.lastOnlineMins
);

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'bad' }) {
  return (
    <div className="bg-slate-950/60 border border-slate-800/60 rounded-xl px-3 py-2">
      <div className="text-[9px] uppercase tracking-wider text-slate-600">{label}</div>
      <div className={cn(
        'text-xs font-bold tabular-nums',
        tone === 'bad' ? 'text-red-400' : 'text-slate-300'
      )}>
        {value}
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="p-8 border-2 border-dashed border-slate-800 rounded-2xl text-center text-sm text-slate-500 leading-snug">
      <Clock className="w-5 h-5 mx-auto mb-2 opacity-30" />
      {children}
    </div>
  );
}
