import React, { useEffect, useState } from 'react';
import { ArrowRight, TrendingUp } from 'lucide-react';
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
    // 3.8 с — встигаєш прочитати цифри, і воно ще не набридає.
    // setTimeout, а не setInterval: після ручного кліку відлік має
    // початись заново, інакше наступна зміна прийде майже одразу.
    const timer = setTimeout(() => setIndex(i => (i + 1) % FRAMES.length), 3800);
    return () => clearTimeout(timer);
  }, [index]);

  const advance = () => setIndex(i => (i + 1) % FRAMES.length);

  const frame = FRAMES[index];
  const next = FRAMES[(index + 1) % FRAMES.length];
  const spread = ((frame.sell.price - frame.buy.price) / frame.buy.price) * 100;

  return (
    /*
      Ширина обмежена свідомо. Раніше картка розтягувалась на всю колонку
      й через це читалась як таблиця, а не як обʼєкт — саме тому вона
      програвала референсу, хоча вміст був той самий.

      Нахил статичний: одна матриця transform, яку композитор рахує раз.
      Це не та «3D-сцена», від якої я відмовлявся заради швидкості на
      телефоні, — там був рух на кожен кадр.
    */
    <div
      className="relative mx-auto w-full max-w-sm sm:max-w-md"
      style={{ perspective: '1600px' }}
    >
      {/* Розсіяне світло під карткою — воно і дає той «живий» контур */}
      <div
        aria-hidden
        className="absolute -inset-6 rounded-[2.5rem] opacity-60"
        style={{
          background:
            'radial-gradient(60% 50% at 50% 45%, rgb(var(--accent-rgb) / 0.18), transparent 70%)',
        }}
      />

      {/*
        Задній шар — наступна зв'язка в черзі, і на ньому видно її біржі.
        Раніше це була порожня плашка «для об'єму»; тепер зміна кадру
        читається як просування стосу, бо те, що визирало ззаду, справді
        виходить наперед.
      */}
      <div
        key={`b${index}`}
        aria-hidden
        className="card-behind absolute inset-x-8 -top-4 h-20 rounded-3xl bg-slate-900/60 border border-slate-800/70 px-5 pt-2.5 overflow-hidden"
        style={{ transform: 'rotateX(8deg)' }}
      >
        <div className="tag-mono text-[10px] uppercase tracking-widest text-slate-600 truncate">
          далі · {next.buy.exchange} → {next.sell.exchange}
        </div>
      </div>

      <button
        type="button"
        onClick={advance}
        aria-label="Наступна зв'язка"
        key={`f${index}`}
        className="card-advance relative block w-full text-left bg-slate-900/85 border border-accent-500/25 rounded-3xl p-6 backdrop-blur-sm cursor-pointer"
        style={{
          transform: 'rotateX(2deg) rotateY(-3deg)',
          boxShadow:
            '0 0 0 1px rgb(var(--accent-rgb) / 0.12), 0 30px 70px -25px rgb(var(--accent-rgb) / 0.4), 0 24px 60px rgb(2 6 23 / 0.7)',
        }}
      >
        <div className="flex items-center justify-between mb-6">
          <span className="tag-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
            приклад зв'язки
          </span>
          <span className="flex items-center gap-1.5 text-[11px] text-slate-500">
            <span className="relative flex w-1.5 h-1.5">
              <span className="absolute inline-flex w-full h-full rounded-full bg-accent-500 opacity-75 animate-ping" />
              <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-accent-500" />
            </span>
            сканування
          </span>
        </div>

        {/*
          Порядок читання як у референсі: спершу «звідки куди», далі ціни,
          і аж потім різниця — вона тут висновок, а не заголовок.
        */}
        <div key={`p${index}`} className="animate-tick grid grid-cols-[1fr_auto_1fr] items-end gap-3">
          <Leg
            label="Купуєш"
            exchange={frame.buy.exchange}
            price={frame.buy.price}
            tone="down"
          />

          <ArrowRight className="w-4 h-4 text-slate-700 shrink-0 mb-1.5" />

          <Leg
            label="Продаєш"
            exchange={frame.sell.exchange}
            price={frame.sell.price}
            tone="up"
          />
        </div>

        <div
          key={`s${index}`}
          className="animate-tick mt-6 pt-6 border-t border-slate-800/70 flex items-end justify-center gap-2.5"
        >
          <TrendingUp className="w-7 h-7 text-accent-400 shrink-0 mb-2.5" />
          <span className="text-5xl sm:text-6xl font-black text-accent-400 tabular-nums leading-none tracking-tight">
            +{spread.toFixed(2)}%
          </span>
          <span className="tag-mono text-[10px] uppercase tracking-[0.25em] text-slate-500 pb-2">
            спред
          </span>
        </div>

        {/* Індикатор циклу — видно, що це стрічка прикладів, а не одне число */}
        <div className="flex gap-1.5 mt-6">
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
      </button>
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
    <div className={cn('min-w-0', tone === 'up' && 'text-right')}>
      <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-1.5">{label}</div>
      <div className="text-sm font-bold text-white truncate">{exchange}</div>
      <div
        className={cn(
          'text-xl font-black tabular-nums',
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
