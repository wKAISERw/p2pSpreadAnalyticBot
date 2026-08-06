import React, { useMemo, useState } from 'react';
import { motion } from 'motion/react';
import {
  Layers, TrendingDown, TrendingUp, Zap, Gauge, AlertTriangle, CheckCircle2,
  XCircle, ShieldAlert, Megaphone, Info, BadgeCheck,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { useExchanges } from '../../hooks/useExchanges';
import { IncomingOrder, MakerSide, MakerSpeed } from '../../types/maker';
import {
  computeBuyAdvice, computeSellAdvice, demoAd, demoCompetitors, demoIncomingOrders,
} from '../../data/makerDemo';

const uah = (v: number, digits = 2) => `${v.toFixed(digits)} ₴`;

/**
 * Робоче місце maker-режиму.
 *
 * Повторює те, що бот вміє в MAKER_BUY / MAKER_SELL: порада ціни від
 * PriceAdvisor, стакан конкурентів зі «стінкою» ліквідності та стрічка
 * вхідних ордерів з вердиктом ризик-движка (MakerAdMonitor).
 *
 * Дані демонстраційні — HTTP-ендпоінта під maker у боті ще немає.
 * Кнопки нічого не надсилають, лише показують очікуваний сценарій.
 */
export default function MakerWorkspace() {
  const { names: exchangeNames } = useExchanges();
  const [exchange, setExchange] = useState('Bybit');
  const [side, setSide] = useState<MakerSide>('MAKER_SELL');

  // Входи, які в боті беруться з scanner_users: maker_buy_price, target_margin.
  const [buyPrice, setBuyPrice] = useState(41.05);
  const [amountUsdt, setAmountUsdt] = useState(500);
  const [marginPct, setMarginPct] = useState(0.5);
  const [networkFee, setNetworkFee] = useState(1.0);
  const [speed, setSpeed] = useState<MakerSpeed>('FAST');

  const competitors = useMemo(() => demoCompetitors(exchange), [exchange]);

  const sellAdvice = useMemo(
    () => computeSellAdvice(buyPrice, amountUsdt, marginPct / 100, networkFee, competitors, speed),
    [buyPrice, amountUsdt, marginPct, networkFee, competitors, speed]
  );

  const buyAdvice = useMemo(
    () => computeBuyAdvice(competitors[0]?.price ?? 41.3, amountUsdt, marginPct / 100, networkFee),
    [competitors, amountUsdt, marginPct, networkFee]
  );

  const myPrice = side === 'MAKER_SELL' ? sellAdvice.recommendedPrice : buyAdvice.maxBuyPrice;
  const ad = useMemo(() => demoAd(exchange, side, myPrice), [exchange, side, myPrice]);
  const orders = useMemo(() => demoIncomingOrders(exchange), [exchange]);

  return (
    <div className="space-y-4">
      <DemoBanner />

      {/* Панель керування */}
      <div className="bg-slate-900/80 border border-purple-500/20 rounded-2xl p-4 flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <Layers className="w-5 h-5 text-purple-400" />
          <span className="text-sm font-bold text-white">Maker</span>
        </div>

        <div className="h-6 w-px bg-slate-800" />

        <div className="flex bg-slate-950 border border-slate-800 rounded-xl p-1">
          {([
            { value: 'MAKER_BUY', label: 'Купівля', icon: TrendingDown },
            { value: 'MAKER_SELL', label: 'Продаж', icon: TrendingUp },
          ] as const).map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              onClick={() => setSide(value)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors',
                side === value ? 'bg-purple-500/20 text-purple-300' : 'text-slate-500 hover:text-slate-300'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        <div className="h-6 w-px bg-slate-800" />

        <div className="flex items-center gap-1">
          {exchangeNames.slice(0, 5).map(name => (
            <button
              key={name}
              onClick={() => setExchange(name)}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-bold border transition-all',
                exchange === name
                  ? 'bg-purple-500/10 border-purple-500/30 text-purple-300'
                  : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
              )}
            >
              {name}
            </button>
          ))}
        </div>

        {side === 'MAKER_SELL' && (
          <>
            <div className="h-6 w-px bg-slate-800" />
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-400 uppercase tracking-wider">Швидкість</span>
              <div className="flex bg-slate-950 border border-slate-800 rounded-xl p-1">
                {([
                  { value: 'FAST', label: 'FAST', icon: Zap, hint: 'Стати перед стінкою ліквідності' },
                  { value: 'ANY', label: 'ANY', icon: Gauge, hint: 'Просто перебити топ-1' },
                ] as const).map(({ value, label, icon: Icon, hint }) => (
                  <button
                    key={value}
                    onClick={() => setSpeed(value)}
                    title={hint}
                    className={cn(
                      'flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-bold transition-colors',
                      speed === value ? 'bg-slate-800 text-white' : 'text-slate-500 hover:text-slate-300'
                    )}
                  >
                    <Icon className="w-3 h-3" />{label}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Калькулятор */}
        <Card title="Вхідні дані" className="lg:col-span-1">
          {side === 'MAKER_SELL' && (
            <NumField label="Ціна закупівлі (₴)" sub="maker_buy_price" step={0.01} value={buyPrice} onChange={setBuyPrice} />
          )}
          <NumField label="Об'єм (USDT)" sub="amount_usdt" step={10} value={amountUsdt} onChange={setAmountUsdt} />
          <NumField
            label={side === 'MAKER_SELL' ? 'Мін. маржа (%)' : 'Бажана маржа (%)'}
            sub={side === 'MAKER_SELL' ? 'min_margin' : 'target_margin'}
            step={0.1}
            value={marginPct}
            onChange={setMarginPct}
          />
          <NumField label="Мережева комісія (USDT)" sub="network_fee" step={0.1} value={networkFee} onChange={setNetworkFee} />
        </Card>

        {/* Порада */}
        <Card title="Порада" className="lg:col-span-2">
          {side === 'MAKER_SELL' ? (
            <>
              <BigPrice
                label="Рекомендована ціна продажу"
                value={sellAdvice.recommendedPrice}
                tone="emerald"
                caption={sellAdvice.reason}
              />
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                <Stat label="Абсолютний мінімум" value={uah(sellAdvice.absoluteMinSell, 4)} />
                <Stat label="Топ конкурента" value={uah(sellAdvice.competitorPrice, 4)} />
                <Stat label="USDT після fee" value={sellAdvice.sellAmountAfterFee.toFixed(2)} />
                <Stat
                  label="Очікуваний профіт"
                  value={uah(sellAdvice.estimatedProfit, 2)}
                  tone={sellAdvice.estimatedProfit > 0 ? 'emerald' : 'red'}
                />
              </div>
              {sellAdvice.recommendedPrice <= sellAdvice.absoluteMinSell && (
                <Warning>
                  Ринок нижчий за твою мінімальну маржу — ціну підтягнуто до беззбитковості.
                  У боті це той самий Market Stop-Loss.
                </Warning>
              )}
            </>
          ) : (
            <>
              <BigPrice
                label="Максимальна ціна купівлі"
                value={buyAdvice.maxBuyPrice}
                tone="blue"
                caption={`Щоб заробити ${buyAdvice.targetMarginPct.toFixed(1)}% при топі продажу ${uah(buyAdvice.sellBookTop, 2)}`}
              />
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                <Stat label="Топ продавця" value={uah(buyAdvice.sellBookTop, 4)} />
                <Stat label="Мережева комісія" value={`${buyAdvice.networkFee.toFixed(2)} USDT`} />
                <Stat label="Бажана маржа" value={`${buyAdvice.targetMarginPct.toFixed(1)}%`} />
                <Stat
                  label="Очікуваний профіт"
                  value={uah(buyAdvice.estimatedProfitUah, 2)}
                  tone={buyAdvice.estimatedProfitUah > 0 ? 'emerald' : 'red'}
                />
              </div>
            </>
          )}

          <button
            onClick={() => toast.info('Демо: тут бот виставив би оголошення з цією ціною')}
            className="mt-5 w-full flex items-center justify-center gap-2 px-6 py-3 bg-purple-500/15 hover:bg-purple-500/25 border border-purple-500/30 text-purple-300 text-sm font-bold rounded-xl transition-colors"
          >
            <Megaphone className="w-4 h-4" />
            Створити / оновити оголошення
          </button>
        </Card>
      </div>

      {/* Стакан */}
      <Card title={`Стакан конкурентів · ${exchange}`}>
        <p className="text-xs text-slate-500 -mt-2 mb-4">
          Позначено ціну, перед якою вже накопичено {sellAdvice.wallVolumeUsdt} USDT —
          ставати за неї немає сенсу, черга попереду не розсмокчеться.
        </p>
        <OrderBook
          competitors={competitors}
          myPrice={myPrice}
          wallPrice={sellAdvice.wallPrice}
          side={side}
        />
      </Card>

      {/* Оголошення + вхідні ордери */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="Моє оголошення" className="lg:col-span-1">
          <div className="space-y-3">
            <RowStat label="ID" value={ad.itemId} mono />
            <RowStat label="Ціна" value={uah(ad.price, 4)} />
            <RowStat label="Залишок" value={`${ad.remainingUsdt} / ${ad.totalUsdt} USDT`} />
            <RowStat label="Позиція в стакані" value={`#${ad.bookPosition}`} />
            <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-purple-500 rounded-full"
                style={{ width: `${(ad.remainingUsdt / ad.totalUsdt) * 100}%` }}
              />
            </div>
            <div className="flex items-center gap-2 text-xs">
              <span className={cn('w-2 h-2 rounded-full', ad.isOnline ? 'bg-accent-500' : 'bg-slate-600')} />
              <span className="text-slate-400">{ad.isOnline ? 'Онлайн' : 'Офлайн'}</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-400">попереду {ad.competitorsAhead}</span>
            </div>
          </div>
        </Card>

        <Card title="Вхідні ордери" className="lg:col-span-2">
          <div className="space-y-3">
            {orders.map(order => <IncomingOrderCard key={order.orderId} order={order} />)}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ─── Стакан ───────────────────────────────────────────────────────────────

function OrderBook({
  competitors, myPrice, wallPrice, side,
}: {
  competitors: ReturnType<typeof demoCompetitors>;
  myPrice: number;
  wallPrice: number;
  side: MakerSide;
}) {
  const maxVolume = Math.max(...competitors.map(c => c.maxLimit), 1);

  // Наша ціна вставляється в стакан на своє місце, щоб було видно позицію.
  const rows: Array<{ kind: 'me' } | { kind: 'them'; order: (typeof competitors)[number] }> = [];
  let inserted = false;
  for (const order of competitors) {
    if (!inserted && myPrice <= order.price) {
      rows.push({ kind: 'me' });
      inserted = true;
    }
    rows.push({ kind: 'them', order });
  }
  if (!inserted) rows.push({ kind: 'me' });

  return (
    <div className="space-y-1">
      {rows.map((row, i) => {
        if (row.kind === 'me') {
          return (
            <div
              key="me"
              className="relative flex items-center justify-between px-4 py-2.5 rounded-xl bg-purple-500/15 border border-purple-500/40"
            >
              <div className="flex items-center gap-2 text-sm font-bold text-purple-200">
                <Megaphone className="w-4 h-4" />
                Твоє оголошення
              </div>
              <div className="text-sm font-black text-purple-200 tabular-nums">{uah(myPrice, 4)}</div>
            </div>
          );
        }

        const { order } = row;
        const isWall = Math.abs(order.price - wallPrice) < 0.00005 && side === 'MAKER_SELL';

        return (
          <div
            key={`${order.merchantId}-${i}`}
            className={cn(
              'relative flex items-center justify-between px-4 py-2 rounded-xl border overflow-hidden',
              isWall ? 'border-orange-500/40 bg-orange-500/5' : 'border-slate-800/50 bg-slate-950/40'
            )}
          >
            <div
              className="absolute inset-y-0 left-0 bg-slate-800/40 pointer-events-none"
              style={{ width: `${(order.maxLimit / maxVolume) * 100}%` }}
            />

            <div className="relative flex items-center gap-2 min-w-0">
              <span className="text-sm text-slate-200 truncate">{order.merchantName}</span>
              {order.isVerified && <BadgeCheck className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
              <span className="text-[11px] text-slate-500 tabular-nums shrink-0">
                {order.orderCount} · {order.finishRate}%
              </span>
              {isWall && (
                <span className="text-[10px] font-black uppercase tracking-wider text-orange-400 shrink-0">
                  стінка
                </span>
              )}
            </div>

            <div className="relative flex items-center gap-4 shrink-0">
              <span className="text-[11px] text-slate-500 tabular-nums hidden sm:block">
                до {Math.round(order.maxLimit).toLocaleString('uk-UA')} ₴
              </span>
              <span className="text-sm font-bold text-slate-200 tabular-nums">{uah(order.price, 4)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Вхідний ордер ────────────────────────────────────────────────────────

const VERDICT_STYLE = {
  OK: { badge: 'bg-accent-500/10 text-accent-400 border-accent-500/30', icon: CheckCircle2, label: 'Безпечно' },
  WARN: { badge: 'bg-orange-500/10 text-orange-400 border-orange-500/30', icon: AlertTriangle, label: 'Обережно' },
  BLOCK: { badge: 'bg-red-500/10 text-red-400 border-red-500/30', icon: ShieldAlert, label: 'Блок' },
  PENDING: { badge: 'bg-slate-800 text-slate-400 border-slate-700', icon: Info, label: 'Аналізую' },
} as const;

const IncomingOrderCard: React.FC<{ order: IncomingOrder }> = ({ order }) => {
  const style = VERDICT_STYLE[order.rec] ?? VERDICT_STYLE.PENDING;
  const Icon = style.icon;
  const secondsAgo = Math.max(0, Math.round((Date.now() - order.createdAt) / 1000));

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-950/50 border border-slate-800/50 rounded-2xl p-4"
    >
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-bold text-white truncate">{order.counterparty.merchantName}</span>
            {order.counterparty.isVerified && <BadgeCheck className="w-4 h-4 text-blue-400 shrink-0" />}
          </div>
          <div className="text-[11px] text-slate-500 tabular-nums">
            {order.counterparty.finishRate}% · {order.counterparty.monthOrderCount} угод · {secondsAgo}с тому
          </div>
        </div>

        <div className="text-right shrink-0">
          <div className="font-black text-white tabular-nums">
            {Math.round(order.totalFiat).toLocaleString('uk-UA')} ₴
          </div>
          <div className="text-[11px] text-slate-500 tabular-nums">
            {order.amountUsdt} USDT × {order.price}
          </div>
        </div>
      </div>

      <div className={cn('flex items-start gap-2 px-3 py-2 rounded-xl border mb-3', style.badge)}>
        <Icon className="w-4 h-4 shrink-0 mt-0.5" />
        <div className="min-w-0">
          <div className="text-xs font-bold">{style.label}</div>
          <div className="text-[11px] opacity-90 leading-snug">{order.reason}</div>
        </div>
      </div>

      {order.counterparty.riskFlag && (
        <div className="text-[10px] font-mono text-slate-500 mb-3 truncate" title={order.counterparty.riskFlag}>
          {order.counterparty.riskFlag}
        </div>
      )}

      <div className="flex gap-2">
        <button
          onClick={() => toast.success(`Демо: ордер ${order.orderId.slice(-6)} прийнято`)}
          className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2 bg-accent-500/10 hover:bg-accent-500/20 border border-accent-500/30 text-accent-400 text-xs font-bold rounded-xl transition-colors"
        >
          <CheckCircle2 className="w-3.5 h-3.5" /> Прийняти
        </button>
        <button
          onClick={() => toast.info(`Демо: ордер ${order.orderId.slice(-6)} відхилено`)}
          className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-400 text-xs font-bold rounded-xl transition-colors"
        >
          <XCircle className="w-3.5 h-3.5" /> Відхилити
        </button>
      </div>
    </motion.div>
  );
}

// ─── Дрібниці ─────────────────────────────────────────────────────────────

function DemoBanner() {
  return (
    <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-purple-500/10 border border-purple-500/30">
      <AlertTriangle className="w-4 h-4 text-purple-400 shrink-0" />
      <span className="text-xs font-bold text-purple-300">
        ДЕМО — розрахунки за формулами PriceAdvisor, ринкові дані синтетичні, кнопки нічого не надсилають
      </span>
    </div>
  );
}

function Warning({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-4 flex items-start gap-2 px-4 py-2.5 rounded-xl bg-orange-500/10 border border-orange-500/30">
      <AlertTriangle className="w-4 h-4 text-orange-400 shrink-0 mt-0.5" />
      <span className="text-xs text-orange-300 leading-snug">{children}</span>
    </div>
  );
}

function Card({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('bg-slate-900/50 border border-slate-800/50 rounded-2xl p-5', className)}>
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-4">{title}</h3>
      {children}
    </section>
  );
}

function NumField({
  label, sub, value, onChange, step,
}: {
  label: string; sub: string; value: number; onChange: (v: number) => void; step: number;
}) {
  return (
    <div className="mb-3 last:mb-0">
      <div className="text-xs font-bold text-white">{label}</div>
      <div className="text-[10px] text-slate-500 font-mono mb-1.5">{sub}</div>
      <input
        type="number"
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm font-bold text-white focus:border-purple-500 focus:ring-2 focus:ring-purple-500/40 outline-none transition-all tabular-nums"
      />
    </div>
  );
}

function BigPrice({
  label, value, tone, caption,
}: {
  label: string; value: number; tone: 'emerald' | 'blue'; caption: string;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-1">{label}</div>
      <div className={cn(
        'text-4xl font-black tabular-nums',
        tone === 'emerald' ? 'text-accent-400' : 'text-blue-400'
      )}>
        {value.toFixed(4)} ₴
      </div>
      <div className="text-xs text-slate-400 mt-1.5">{caption}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'emerald' | 'red' }) {
  return (
    <div className="bg-slate-950/50 border border-slate-800/50 rounded-xl p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{label}</div>
      <div className={cn(
        'text-sm font-bold tabular-nums',
        tone === 'emerald' ? 'text-accent-400' : tone === 'red' ? 'text-red-400' : 'text-white'
      )}>
        {value}
      </div>
    </div>
  );
}

function RowStat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-slate-400">{label}</span>
      <span className={cn('font-bold text-white', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  );
}
