import React from 'react';
import { cn } from '../lib/utils';

/**
 * Поверхні секцій.
 *
 * Однотипність лендингу бралася не з текстів, а з того, що кожен блок
 * малювався однаково: картка на bg-slate-900/40 з бордером і однаковим
 * радіусом. Коли всі блоки мають однакову візуальну вагу, оку немає за
 * що зачепитись — сторінка читається як одна сіра стрічка.
 *
 * Тут кілька різних поверхонь, щоб секції чергувались за фактурою, а не
 * лише за вмістом. Правило просте: дві сусідні секції не мають бути
 * одного типу.
 */

type SurfaceKind = 'plain' | 'panel' | 'dots' | 'grid' | 'scan';

export function Surface({
  kind = 'panel',
  className,
  children,
  noise = false,
}: {
  kind?: SurfaceKind;
  className?: string;
  children: React.ReactNode;
  noise?: boolean;
}) {
  return (
    <div
      className={cn(
        'relative overflow-hidden',
        kind === 'plain' && 'bg-transparent',
        kind === 'panel' &&
          'bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl rounded-[2rem] shadow-xl shadow-slate-950/50',
        kind === 'dots' &&
          'surface-dots bg-slate-950/70 border border-slate-800/60 rounded-[2rem]',
        kind === 'grid' &&
          'surface-grid bg-slate-900/40 border border-slate-800/60 rounded-[2rem]',
        kind === 'scan' &&
          'surface-scan bg-slate-950/80 border border-accent-500/20 rounded-[2rem]',
        noise && 'surface-noise',
        className
      )}
    >
      {children}
    </div>
  );
}

/**
 * Потік «даних» у фоні блока. Кількість смуг мала свідомо: це акцент,
 * а не заставка, і кожна смуга — окремий елемент, що анімується.
 */
export function DataRain({ count = 14 }: { count?: number }) {
  // Позиції та темпи детерміновані: випадковість на кожен рендер
  // означала б, що при будь-якому оновленні стану дощ смикається.
  const drops = React.useMemo(
    () =>
      Array.from({ length: count }, (_, i) => ({
        left: `${(i * 100) / count + ((i * 37) % 7)}%`,
        duration: `${4 + ((i * 13) % 7)}s`,
        delay: `${-((i * 17) % 9)}s`,
      })),
    [count]
  );

  return (
    <div className="data-rain" aria-hidden>
      {drops.map((d, i) => (
        <span
          key={i}
          style={{ left: d.left, animationDuration: d.duration, animationDelay: d.delay }}
        />
      ))}
    </div>
  );
}

/** Моноширинний ярлик секції — «технічний» акцент замість ще одного eyebrow. */
export function MonoTag({ children }: { children: React.ReactNode }) {
  return (
    <span className="tag-mono inline-flex items-center gap-2 px-3 py-1 rounded-lg bg-slate-950/80 border border-slate-800 text-[11px] uppercase text-accent-400">
      <span className="w-1.5 h-1.5 bg-accent-500" />
      {children}
    </span>
  );
}

/**
 * Велика цифра з підписом. Дає секції точку опори — без неї блок із
 * самого тексту читається як суцільна маса.
 */
export function Metric({
  value,
  label,
  hint,
}: {
  value: string;
  label: string;
  hint?: string;
}) {
  return (
    <div className="pixel-frame px-4 py-4">
      <div className="text-3xl sm:text-4xl font-black text-white tabular-nums leading-none mb-2">
        {value}
      </div>
      <div className="text-[11px] font-bold uppercase tracking-wider text-accent-400 mb-1">
        {label}
      </div>
      {hint && <div className="text-[11px] text-slate-500 leading-snug">{hint}</div>}
    </div>
  );
}
