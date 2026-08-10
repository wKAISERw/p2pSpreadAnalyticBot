import React, { useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import {
  ExternalLink, Copy, ShieldAlert, BadgeCheck, Clock, Gift, TrendingDown, TrendingUp,
  Smartphone, Filter, ChevronDown, Wallet, Percent,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { useAppStore } from '../../store';
import {
  Bank, BuyBudget, Card, CardDisplaySettings, OrderRejection, TakerOrder,
  TakerOrdersResponse,
} from '../../types';
import { RiskPanel } from '../RiskPanel';
import { BankChips } from '../BankChips';
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


const uah = (v: number) => `${v.toFixed(2)} ₴`;

export function TakerOrdersList({ side }: { side: 'buy' | 'sell' | 'both' }) {
  const telegramId = useAppStore(state => state.auth?.telegramId);

  const { data, error, isLoading } = useSWR<TakerOrdersResponse>(
    telegramId ? ['/taker/orders', side] : null,
    () => api.getTakerOrders(side),
    { refreshInterval: 10000, shouldRetryOnError: false }
  );

  // Ключ той самий, що в налаштуваннях карткового модуля — SWR віддає
  // спільний кеш, а не другий запит. Поки налаштування не долетіло,
  // показуємо: сховати те, що людина просила бачити, гірше за навпаки.
  const { data: display } = useSWR<CardDisplaySettings>(
    '/user/card-display',
    () => api.getCardDisplay(),
    { shouldRetryOnError: false, revalidateOnFocus: false }
  );
  const showRejected = display?.showRejectedOrders !== 'hide';

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
      <div className="space-y-4">
        <BudgetNote budget={data?.budget ?? null} />
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <Column
            title="Купівля" hint="ти купуєш USDT" side="buy"
            orders={data?.buy ?? []} rejected={data?.rejected?.buy ?? []}
            showRejected={showRejected}
          />
          <Column
            title="Продаж" hint="ти продаєш USDT" side="sell"
            orders={data?.sell ?? []} rejected={data?.rejected?.sell ?? []}
            showRejected={showRejected}
          />
        </div>
      </div>
    );
  }

  const orders = side === 'buy' ? data?.buy ?? [] : data?.sell ?? [];
  const rejected = (side === 'buy' ? data?.rejected?.buy : data?.rejected?.sell) ?? [];
  return (
    <div className="space-y-4">
      {side === 'buy' && <BudgetNote budget={data?.budget ?? null} />}
      <Column
        title={side === 'buy' ? 'Купівля' : 'Продаж'}
        hint={side === 'buy' ? 'ти купуєш USDT' : 'ти продаєш USDT'}
        side={side}
        orders={orders}
        rejected={rejected}
        showRejected={showRejected}
      />
    </div>
  );
}

/**
 * Скільки виходить сьогодні проти того, що введено.
 *
 * Раніше цієї різниці не існувало як поняття: авто-масштабування просто
 * перезаписувало введену суму в базі, і 700 USDT назавжди ставали 480 —
 * навіть після поповнення картки. Тепер бажане недоторкане, але тоді його
 * треба показати поруч із фактичним, інакше незрозуміло, чому бот заходить
 * не на ту суму, яку бачить людина в налаштуваннях.
 *
 * Формулювання залежить від того, чи ввімкнено кошики: «в одному банку» —
 * опис СТАРОЇ межі движка, і після вмикання міжбанківського набору ця
 * фраза почала б описувати обмеження, якого вже немає.
 */
function BudgetNote({ budget }: { budget: BuyBudget | null }) {
  if (!budget || (!budget.scaled && !budget.blocked)) return null;

  const money = (v: number) => Math.round(v).toLocaleString('uk-UA');

  // Звідки взялась стеля. Три різні речення для трьох різних причин —
  // інакше людина не зрозуміє, що саме змінити.
  const source = budget.interBank
    ? 'на всіх картках разом'
    : budget.bestBank
      ? `в одному банку (${budget.bestBank})`
      : 'в одному банку';

  // Кошики вимкнені, а грошей на картках більше, ніж стеля однієї угоди —
  // тобто впираємось саме в межу «один банк», і її можна зняти.
  const capIsTheLimit =
    !budget.interBank && budget.totalUah > budget.availableUah + 1;

  return (
    <div
      className={cn(
        'flex items-start gap-2.5 px-4 py-3 rounded-2xl border',
        budget.blocked
          ? 'bg-red-500/10 border-red-500/20'
          : 'bg-orange-500/10 border-orange-500/20'
      )}
    >
      <Wallet
        className={cn(
          'w-4 h-4 shrink-0 mt-0.5',
          budget.blocked ? 'text-red-400' : 'text-orange-400'
        )}
      />
      <div className="text-[11px] leading-relaxed">
        {budget.blocked ? (
          <span className="text-red-300">
            Доступно {money(budget.availableUah)} ₴ {source} — не набирається
            навіть мінімальна угода. Бажані {budget.desiredUsdt} ₮ збережені
            й чекають поповнення.
          </span>
        ) : (
          <span className="text-orange-200">
            Заходимо на <b>{budget.effectiveUsdt} ₮</b> замість{' '}
            <b>{budget.desiredUsdt} ₮</b>: доступно{' '}
            {money(budget.availableUah)} ₴ {source}. Введена сума не змінена —
            щойно балансу вистачить, бот знову шукатиме повні{' '}
            {budget.desiredUsdt} ₮.
          </span>
        )}

        {capIsTheLimit && (
          <div className="text-slate-400 mt-1">
            На картках усього {money(budget.totalUah)} ₴, але зібрати суму з
            різних банків движок зараз не буде: «Можливості» → «Картки та
            маршрути» → «Кошики карток між банками».
          </div>
        )}
      </div>
    </div>
  );
}

function Column({
  title, hint, side, orders, rejected, showRejected,
}: {
  title: string;
  hint: string;
  side: 'buy' | 'sell';
  orders: TakerOrder[];
  rejected: OrderRejection[];
  showRejected: boolean;
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
        rejected.length > 0 && showRejected ? (
          // Порожня колонка при непорожньому стакані — не те саме, що
          // порожній ринок, і раніше виглядала однаково.
          <Empty>
            Ринок дав {rejected.length}{' '}
            {rejected.length === 1 ? 'ордер' : 'ордерів'}, але картки їх не
            пропустили. Причини нижче.
          </Empty>
        ) : (
          <Empty>
            Нічого не проходить твої фільтри. Послаб пороги в «Фільтрах» —
            або зачекай наступного циклу.
          </Empty>
        )
      ) : (
        <div className="space-y-3">
          {orders.map(order => (
            <OrderCard key={`${order.exchange}-${order.id}`} order={order} side={side} />
          ))}
        </div>
      )}

      {showRejected && <RejectedSummary rejected={rejected} />}
    </div>
  );
}

/**
 * Що відсіяли картки — зведенням, а не списком.
 *
 * Показувати кожен відкинутий ордер окремо немає сенсу: у стакані їх
 * десятки, а корисна тут одна річ — чи впираємось ми в баланс, у ліміт, чи
 * просто в те, що мерчанти приймають не ті банки.
 */
function RejectedSummary({ rejected }: { rejected: OrderRejection[] }) {
  const [open, setOpen] = useState(false);
  if (rejected.length === 0) return null;

  // Найближчий до проходження ордер — той, де бракує найменше. Саме він
  // цікавий: він каже, наскільки саме не дотягуємо.
  const closest = rejected
    .filter(r => r.shortfallUah > 0)
    .sort((a, b) => a.shortfallUah - b.shortfallUah)[0];

  const groups = new Map<string, { reason: string; count: number; worst: number }>();
  for (const r of rejected) {
    const g = groups.get(r.code) ?? { reason: r.reason, count: 0, worst: 0 };
    g.count += 1;
    if (r.shortfallUah > g.worst) {
      g.worst = r.shortfallUah;
      g.reason = r.reason;
    }
    groups.set(r.code, g);
  }

  const rows = [...groups.entries()].sort((a, b) => b[1].count - a[1].count);

  return (
    <div className="rounded-2xl bg-slate-900/40 border border-slate-800/60 overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-slate-800/30 transition-colors"
      >
        <Filter className="w-3.5 h-3.5 text-slate-500 shrink-0" />
        <span className="text-[11px] font-bold text-slate-400">
          Відсіяно картками: {rejected.length}
        </span>
        <ChevronDown
          className={cn(
            'w-3.5 h-3.5 text-slate-600 ml-auto transition-transform',
            open && 'rotate-180'
          )}
        />
      </button>

      {open && (
        <div className="px-4 pb-3 space-y-1.5">
          {rows.map(([code, g]) => (
            <div key={code} className="flex items-baseline gap-2 text-[11px]">
              <span className="tabular-nums text-slate-500 shrink-0">{g.count}×</span>
              <span className="text-slate-400 leading-snug">{g.reason}</span>
            </div>
          ))}

          {closest && (
            <div className="mt-2 pt-2 border-t border-slate-800 text-[11px] text-slate-500 leading-snug">
              Найближчий — {closest.merchantName} по {uah(closest.price)}:
              бракує {Math.round(closest.shortfallUah).toLocaleString('uk-UA')} ₴
            </div>
          )}
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
          {/* Курс із комісією — саме його треба порівнювати між ордерами:
              при спреді 0.5–1% комісія банку 2% з'їдає весь профіт. */}
          {order.transferFee && (
            <div
              className="text-[11px] text-orange-400 tabular-nums"
              title={`${order.transferFee.description} — від ${Math.round(order.transferFee.onAmountUah).toLocaleString('uk-UA')} ₴`}
            >
              з комісією {uah(order.transferFee.effectivePrice)}
            </div>
          )}
          <div className="text-[11px] text-slate-500 tabular-nums">
            {Math.round(order.minLimit).toLocaleString('uk-UA')}–
            {Math.round(order.maxLimit).toLocaleString('uk-UA')} ₴
          </div>
        </div>
      </div>

      {order.transferFee && (
        <div className="flex items-start gap-1.5 mb-3 px-3 py-2 rounded-xl bg-slate-950/50 border border-slate-800/60">
          <Percent className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" />
          <span className="text-[11px] text-slate-400 leading-snug">
            {order.transferFee.description}: −
            {Math.round(order.transferFee.amountUah).toLocaleString('uk-UA')} ₴
            за переказ у {order.transferFee.bank}
          </span>
        </div>
      )}

      {/* Банки мерчанта, і одразу видно, під які з них у нас є картка.
          Список приймання сам по собі мало що каже: важливо не «які банки
          він приймає», а «чи є серед них мій». */}
      {fields.banks && (order.bankCodes?.length ?? 0) > 0 && (
        <div className="mb-3">
          <BankChips codes={order.bankCodes} />
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
        <div className="mb-3">
          <RiskPanel riskFlag={order.riskFlag} score={order.compositeScore} />
        </div>
      )}

      {fields.terms && order.tradeTerms && (
        <TradeTerms text={order.tradeTerms} />
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
    prev.order.lastOnlineMins === next.order.lastOnlineMins &&
    prev.order.transferFee?.effectivePrice === next.order.transferFee?.effectivePrice
);

/**
 * Умови мерчанта: перші рядки одразу, повний текст — за кліком.
 *
 * У боті для цього два окремі перемикачі («Вижимка умов» і «Повні умови
 * під спойлером»), бо там кожен зайвий абзац розсуває повідомлення. На
 * сторінці розгортання нічого не ламає, тож достатньо однієї згортки —
 * але сам текст мусить бути доступний повністю: саме в ньому мерчант
 * пише про третіх осіб, ФОП і вимогу писати в чат.
 */
function TradeTerms({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const isLong = text.length > 180;

  return (
    <div className="mb-3">
      <p className={cn(
        'text-[11px] text-slate-400 leading-snug whitespace-pre-line',
        !open && isLong && 'line-clamp-3'
      )}>
        {text}
      </p>
      {isLong && (
        <button
          onClick={() => setOpen(v => !v)}
          className="mt-1 text-[11px] font-bold text-slate-500 hover:text-slate-300 transition-colors"
        >
          {open ? 'Згорнути умови' : 'Показати повні умови'}
        </button>
      )}
    </div>
  );
}

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
