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

/**
 * Де лежить картка залежно від того, котра вона за чергою.
 *
 * Нахил у кожної свій і в різні боки — саме це відрізняє стос карт у
 * руці від рівної стопки паперу. Задні ще й зсунуті вбік, щоб визирали
 * не тільки верхнім краєм.
 */
const SLOTS = [
  { y: 0, x: 0, scale: 1, rot: 0, opacity: 1, z: 30 },
  { y: -20, x: 10, scale: 0.95, rot: 1.6, opacity: 0.55, z: 20 },
  { y: -36, x: -8, scale: 0.9, rot: -1.8, opacity: 0.28, z: 10 },
];

export default function SpreadVisual() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    // setTimeout із залежністю від індексу, а не setInterval: після
    // ручного кліку відлік має початись заново, інакше автоматична зміна
    // прилетіла б майже одразу після натискання.
    const timer = setTimeout(() => setIndex(i => (i + 1) % FRAMES.length), 4200);
    return () => clearTimeout(timer);
  }, [index]);

  const advance = () => setIndex(i => (i + 1) % FRAMES.length);

  return (
    <div
      className="relative mx-auto w-full max-w-sm sm:max-w-md"
      style={{ perspective: '1600px' }}
    >
      {/* Розсіяне світло під стосом — воно і дає той «живий» контур */}
      <div
        aria-hidden
        className="absolute -inset-6 rounded-[2.5rem] opacity-60"
        style={{
          background:
            'radial-gradient(60% 50% at 50% 45%, rgb(var(--accent-rgb) / 0.18), transparent 70%)',
        }}
      />

      {/*
        Розпірка задає висоту стосу. Усі справжні картки абсолютні —
        інакше при зміні порядку та з них, що тримає висоту, мінялась би,
        і блок стрибав би на кожному тасуванні.
      */}
      <div className="invisible" aria-hidden>
        <Card frame={FRAMES[0]} />
      </div>

      {FRAMES.map((frame, i) => {
        // Наскільки далеко ця картка від верху стосу
        const slot = (i - index + FRAMES.length) % FRAMES.length;
        const s = SLOTS[slot] ?? SLOTS[SLOTS.length - 1];
        const isFront = slot === 0;

        return (
          <button
            key={i}
            type="button"
            onClick={advance}
            tabIndex={isFront ? 0 : -1}
            aria-hidden={!isFront}
            aria-label="Наступна зв'язка"
            /*
              Тасування робиться переходом між станами, а не перезапуском
              анімації через key. Раніше картка перемонтовувалась щоразу:
              весь вміст будувався наново, паралельно грали власні
              анімації рядків — звідси й смикання. Тепер вузли ті самі,
              міняються тільки transform і opacity, тобто те, що браузер
              рахує на композиторі.
            */
            className={cn(
              'absolute inset-x-0 top-0 block w-full text-left rounded-3xl p-6',
              'bg-slate-900/85 border border-accent-500/25 backdrop-blur-sm',
              'transition-[transform,opacity] duration-[600ms]',
              isFront ? 'cursor-pointer' : 'pointer-events-none'
            )}
            style={{
              zIndex: s.z,
              opacity: s.opacity,
              transform: `translate3d(${s.x}px, ${s.y}px, 0) scale(${s.scale}) rotate(${s.rot}deg) rotateX(2deg)`,
              transitionTimingFunction: 'cubic-bezier(0.34, 1.3, 0.5, 1)',
              boxShadow: isFront
                ? '0 0 0 1px rgb(var(--accent-rgb) / 0.12), 0 30px 70px -25px rgb(var(--accent-rgb) / 0.4), 0 24px 60px rgb(2 6 23 / 0.7)'
                : '0 18px 40px rgb(2 6 23 / 0.5)',
            }}
          >
            <Card frame={frame} active={isFront} position={index} total={FRAMES.length} />
          </button>
        );
      })}
    </div>
  );
}

/** Вміст картки. Винесено, щоб розпірка й стос малювали те саме. */
const Card: React.FC<{
  frame: Frame;
  active?: boolean;
  position?: number;
  total?: number;
}> = ({ frame, active, position = 0, total = 1 }) => {
  const spread = ((frame.sell.price - frame.buy.price) / frame.buy.price) * 100;

  return (
    <>
      <div className="flex items-center justify-between mb-6">
        <span className="tag-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
          приклад зв'язки
        </span>
        <span className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <span className="relative flex w-1.5 h-1.5">
            {active && (
              <span className="absolute inline-flex w-full h-full rounded-full bg-accent-500 opacity-75 animate-ping" />
            )}
            <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-accent-500" />
          </span>
          сканування
        </span>
      </div>

      {/*
        Порядок читання: спершу «звідки куди», далі ціни, і аж потім
        різниця — вона тут висновок, а не заголовок.
      */}
      <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
        <Leg label="Купуєш" exchange={frame.buy.exchange} price={frame.buy.price} tone="down" />
        <ArrowRight className="w-4 h-4 text-slate-700 shrink-0 mb-1.5" />
        <Leg label="Продаєш" exchange={frame.sell.exchange} price={frame.sell.price} tone="up" />
      </div>

      <div className="mt-6 pt-6 border-t border-slate-800/70 flex items-end justify-center gap-2.5">
        <TrendingUp className="w-7 h-7 text-accent-400 shrink-0 mb-2.5" />
        <span className="text-5xl sm:text-6xl font-black text-accent-400 tabular-nums leading-none tracking-tight">
          +{spread.toFixed(2)}%
        </span>
        <span className="tag-mono text-[10px] uppercase tracking-[0.25em] text-slate-500 pb-2">
          спред
        </span>
      </div>

      {/* Індикатор циклу — видно, що це стос прикладів, а не одне число */}
      <div className="flex gap-1.5 mt-6">
        {Array.from({ length: total }, (_, i) => (
          <span
            key={i}
            className={cn(
              'h-0.5 rounded-full transition-all duration-500',
              i === position ? 'flex-[3] bg-accent-500' : 'flex-1 bg-slate-800'
            )}
          />
        ))}
      </div>

      <div className="mt-5 pt-5 border-t border-slate-800/70 grid grid-cols-3 gap-3">
        {frame.checks.map(label => (
          <Check key={label} label={label} />
        ))}
      </div>
    </>
  );
};

const Leg: React.FC<{
  label: string;
  exchange: string;
  price: number;
  tone: 'up' | 'down';
}> = ({ label, exchange, price, tone }) => (
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

const Check: React.FC<{ label: string }> = ({ label }) => (
  <div className="flex items-center gap-1.5 min-w-0">
    <span className="w-4 h-4 rounded-full bg-accent-500/15 border border-accent-500/40 flex items-center justify-center shrink-0">
      <span className="w-1.5 h-1.5 rounded-full bg-accent-400" />
    </span>
    <span className="text-[11px] text-slate-400 truncate">{label}</span>
  </div>
);
