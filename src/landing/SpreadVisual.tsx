import React, { useEffect, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * Наочне пояснення механіки в герої.
 *
 * Показує рівно те, що робить сканер: дві біржі з різними цінами на той
 * самий USDT і різницю між ними. Цифри ілюстративні й підписані як приклад
 * — підставляти сюди «живі» дані було б обіцянкою, якої сторінка не може
 * дотримати: у кожного свої фільтри, капітал і банки.
 *
 * Рух — на CSS-класах і одному таймері. Це перший екран, він має малюватись
 * швидко на слабкому телефоні.
 */

interface Frame {
  buy: { exchange: string; price: number };
  sell: { exchange: string; price: number };
  checks: string[];
}

const FRAMES: Frame[] = [
  {
    buy: { exchange: 'Bybit', price: 41.02 },
    sell: { exchange: 'OKX', price: 41.68 },
    checks: ['Ботів відсіяно', 'Відгуки прочитано', 'Ліміти зведено'],
  },
  {
    buy: { exchange: 'Binance', price: 40.88 },
    sell: { exchange: 'MEXC', price: 41.44 },
    checks: ['Умови розібрано', 'Чорний список чистий', 'Обсяг проходить'],
  },
  {
    buy: { exchange: 'OKX', price: 41.15 },
    sell: { exchange: 'Binance', price: 41.97 },
    checks: ['Поведінка в нормі', 'Скарг немає', 'Картка вільна'],
  },
];

export default function SpreadVisual() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    // 3.4 с — встигаєш прочитати цифри, і воно ще не набридає.
    const timer = setInterval(() => setIndex(i => (i + 1) % FRAMES.length), 3400);
    return () => clearInterval(timer);
  }, []);

  const frame = FRAMES[index];
  const spread = ((frame.sell.price - frame.buy.price) / frame.buy.price) * 100;

  return (
    <div className="relative">
      <div className="relative bg-slate-900/70 border border-slate-800 rounded-3xl p-6 backdrop-blur-sm shadow-2xl shadow-slate-950/50">
        <div className="flex items-center justify-between mb-6">
          <span className="text-xs font-bold uppercase tracking-widest text-slate-500">
            Приклад зв'язки
          </span>
          <span className="flex items-center gap-1.5 text-xs text-slate-500">
            <span className="relative flex w-1.5 h-1.5">
              <span className="absolute inline-flex w-full h-full rounded-full bg-accent-500 opacity-75 animate-ping" />
              <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-accent-500" />
            </span>
            сканування
          </span>
        </div>

        {/*
          Спред — головне число на всій сторінці, тому воно стоїть окремо
          й крупно, а не втиснуте між двома ногами. Раніше всі три
          елементи були одного розміру, і око не знало, за що чіплятись.
        */}
        <div key={`s${index}`} className="animate-tick text-center mb-5">
          <div className="text-5xl sm:text-6xl font-black text-accent-400 tabular-nums leading-none tracking-tight">
            +{spread.toFixed(2)}%
          </div>
          <div className="tag-mono text-[10px] uppercase tracking-[0.25em] text-slate-500 mt-2">
            чистий спред
          </div>
        </div>

        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          <Leg
            key={`b${index}`}
            label="Купуєш"
            exchange={frame.buy.exchange}
            price={frame.buy.price}
            tone="down"
          />

          <ArrowRight className="w-4 h-4 text-slate-700 shrink-0" />

          <Leg
            key={`s2${index}`}
            label="Продаєш"
            exchange={frame.sell.exchange}
            price={frame.sell.price}
            tone="up"
          />
        </div>

        {/* Індикатор циклу — видно, що це стрічка прикладів, а не одне число */}
        <div className="flex gap-1.5 mt-5">
          {FRAMES.map((_, i) => (
            <span
              key={i}
              className={cn(
                'h-0.5 rounded-full transition-all duration-500',
                i === index ? 'flex-[3] bg-accent-500' : 'flex-1 bg-slate-800'
              )}
            />
          ))}
        </div>

        <div className="mt-5 pt-5 border-t border-slate-800/70 grid grid-cols-3 gap-3">
          {frame.checks.map((label, i) => (
            <Check key={`${index}-${i}`} label={label} delay={i * 80} />
          ))}
        </div>
      </div>
    </div>
  );
}

const Leg: React.FC<{
  label: string;
  exchange: string;
  price: number;
  tone: 'up' | 'down';
}> = ({ label, exchange, price, tone }) => {
  return (
    <div className={cn('min-w-0 animate-tick', tone === 'up' && 'text-right')}>
      <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1">{label}</div>
      <div className="text-sm font-bold text-white truncate">{exchange}</div>
      <div
        className={cn(
          'text-lg sm:text-xl font-black tabular-nums',
          tone === 'up' ? 'text-accent-400' : 'text-slate-300'
        )}
      >
        {price.toFixed(2)} ₴
      </div>
    </div>
  );
};

const Check: React.FC<{ label: string; delay: number }> = ({ label, delay }) => {
  return (
    <div
      className="flex items-center gap-1.5 min-w-0 animate-tick"
      style={{ animationDelay: `${delay}ms` }}
    >
      <span className="w-4 h-4 rounded-full bg-accent-500/15 border border-accent-500/40 flex items-center justify-center shrink-0">
        <span className="w-1.5 h-1.5 rounded-full bg-accent-400" />
      </span>
      <span className="text-[11px] text-slate-400 truncate">{label}</span>
    </div>
  );
};
