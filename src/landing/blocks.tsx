import React, { useEffect, useState } from 'react';
import { cn } from '../lib/utils';

/**
 * Блоки, зібрані за референсами редизайну.
 *
 * Спільна ідея референсів — «термінальна» мова: кутові дужки замість
 * рамок, моношрифт на всьому, що є даними, статус кольором. Тримаю їх в
 * одному файлі, бо вони ходять по кількох сторінках і мають лишатись
 * однаковими.
 */

/* ─────────────────────────── Метрика в дужках ───────────────────────── */

/**
 * Число у квадратних дужках.
 *
 * Дужки малюються двома смугами по краях, а не рамкою: рамка замикає
 * блок у ще одну картку, а тут потрібне саме відчуття вирізаного з
 * інтерфейсу фрагмента.
 */
export const BracketMetric: React.FC<{
  value: string;
  label: string;
  hint: string;
}> = ({ value, label, hint }) => (
  <div className="relative px-5 py-6 text-center">
    <span className="absolute left-0 inset-y-0 w-2.5 border-y border-l border-accent-500/35" aria-hidden />
    <span className="absolute right-0 inset-y-0 w-2.5 border-y border-r border-accent-500/35" aria-hidden />

    <div className="text-4xl sm:text-5xl font-black text-accent-400 tabular-nums leading-none mb-2.5">
      {value}
    </div>
    <div className="tag-mono text-[10px] uppercase tracking-[0.2em] text-slate-300 mb-1.5">
      {label}
    </div>
    <div className="text-[11px] text-slate-500 leading-snug">{hint}</div>
  </div>
);

/* ────────────────────────── Рядок вердикту ─────────────────────────── */

export type Verdict = 'BLOCK' | 'WARN' | 'OK';

const VERDICT_STYLE: Record<Verdict, { badge: string; rail: string; icon: string }> = {
  BLOCK: {
    badge: 'bg-red-500/15 text-red-400 border-red-500/30',
    rail: 'bg-red-500',
    icon: 'text-red-400 bg-red-500/10 border-red-500/25',
  },
  WARN: {
    badge: 'bg-orange-500/15 text-orange-400 border-orange-500/30',
    rail: 'bg-orange-500',
    icon: 'text-orange-400 bg-orange-500/10 border-orange-500/25',
  },
  OK: {
    badge: 'bg-accent-500/15 text-accent-400 border-accent-500/30',
    rail: 'bg-accent-500',
    icon: 'text-accent-400 bg-accent-500/10 border-accent-500/25',
  },
};

/**
 * Що ризик-движок бачить у мерчанті — по одному сигналу на рядок.
 *
 * Вердикти беруться з бота: OK, SUSPICIOUS (тут як WARN у підписі) і
 * BLOCK. Кольорова смуга ліворуч дає прочитати колонку по вертикалі, не
 * читаючи текст, — три червоні поспіль видно одразу.
 */
export const VerdictRow: React.FC<{
  verdict: Verdict;
  title: string;
  note: string;
  icon: React.ElementType;
}> = ({ verdict, title, note, icon: Icon }) => {
  const s = VERDICT_STYLE[verdict];

  return (
    <div className="relative flex items-center gap-3.5 pl-4 pr-3 py-3 rounded-xl bg-slate-900/70 border border-slate-800/80 overflow-hidden">
      <span className={cn('absolute left-0 inset-y-0 w-0.5', s.rail)} aria-hidden />

      <span className={cn('w-9 h-9 rounded-lg border flex items-center justify-center shrink-0', s.icon)}>
        <Icon className="w-4 h-4" />
      </span>

      <div className="min-w-0 flex-1">
        <div className="text-sm font-bold text-slate-100 truncate">{title}</div>
        <div className="text-[11px] text-slate-500 truncate">{note}</div>
      </div>

      <span
        className={cn(
          'tag-mono text-[10px] font-bold px-2 py-1 rounded border shrink-0',
          s.badge
        )}
      >
        {verdict}
      </span>
    </div>
  );
};

/* ──────────────────────── Консольна стрічка ────────────────────────── */

type LogTone = 'muted' | 'hit' | 'done';

export interface LogLine {
  tone: LogTone;
  text: string;
}

const TONE_TEXT: Record<LogTone, string> = {
  muted: 'text-slate-400',
  hit: 'text-accent-400 font-bold',
  done: 'text-accent-400',
};

const TONE_DOT: Record<LogTone, string> = {
  muted: 'bg-slate-600',
  hit: 'bg-accent-400',
  done: 'bg-accent-500',
};

/**
 * Лог, що набігає рядок за рядком.
 *
 * Свідомо показує шлях до алерта, а не до угоди. У референсі стрічка
 * закінчувалась «Автоматичний вхід!» і «Угода відкрита: 0.5 BTC» — це
 * пряме протиріччя з тим, що написано на сусідній сторінці: сканер не
 * має доступу до грошей і нічого не купує. Домалювати таке в лог
 * означало б показати функцію, якої немає.
 */
export const ConsoleFeed: React.FC<{ lines: LogLine[]; caption?: string }> = ({
  lines,
  caption,
}) => {
  const [visible, setVisible] = useState(0);

  useEffect(() => {
    // Після останнього рядка тримаємо паузу й починаємо спочатку —
    // інакше блок або застигає, або блимає без кінця.
    const step = visible >= lines.length ? 2200 : 700;
    const timer = setTimeout(
      () => setVisible(v => (v >= lines.length ? 0 : v + 1)),
      step
    );
    return () => clearTimeout(timer);
  }, [visible, lines.length]);

  return (
    <div className="rounded-2xl bg-slate-950/90 border border-slate-800 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-800/80">
        <span className="w-1.5 h-1.5 rounded-full bg-accent-500 animate-pulse" />
        <span className="tag-mono text-[10px] uppercase tracking-widest text-slate-500">
          {caption ?? 'приклад роботи'}
        </span>
      </div>

      {/*
        Висота фіксована під повний список: без цього блок скаче на
        кожен новий рядок і тягне за собою всю сітку сторінки.
      */}
      <div className="relative p-4" style={{ minHeight: `${lines.length * 28 + 16}px` }}>
        {lines.map((line, i) => {
          const shown = i < visible;
          return (
            <div
              key={i}
              className={cn(
                'relative flex items-start gap-3 pl-1 min-w-0 overflow-hidden transition-all duration-300',
                shown ? 'opacity-100 translate-x-0' : 'opacity-0 -translate-x-1'
              )}
              /* min-w-0 обов'язковий: без нього truncate не працює у флексі,
                 бо елемент не стискається нижче min-content — рядок розпирав
                 колонку сітки й тягнув за собою сусідню картку. */
              style={{ height: '28px' }}
            >
              {/* Рейка між крапками — тягнеться лише до вже показаних рядків */}
              {i < lines.length - 1 && (
                <span
                  className={cn(
                    'absolute left-[6px] top-3.5 w-px h-full transition-colors',
                    shown && i + 1 < visible ? 'bg-accent-500/40' : 'bg-slate-800'
                  )}
                  aria-hidden
                />
              )}

              <span
                className={cn(
                  'relative z-10 w-[7px] h-[7px] rounded-full shrink-0 mt-1.5',
                  shown ? TONE_DOT[line.tone] : 'bg-slate-800'
                )}
              />

              <span className={cn('tag-mono text-[11px] leading-4 truncate min-w-0', TONE_TEXT[line.tone])}>
                {line.text}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ───────────────────────────── Спарклайн ───────────────────────────── */

/**
 * Прев'ю графіка прибутку.
 *
 * Точки зашиті й підписані як приклад: тягнути сюди справжні дані
 * означало б показувати чужий результат як типовий. Розмір крихітний,
 * тому це рукописний path, а не бібліотека графіків — recharts тут
 * коштував би сотні кілобайт заради однієї лінії.
 */
export const Sparkline: React.FC<{ title: string }> = ({ title }) => {
  const points = [8, 14, 11, 19, 17, 26, 23, 34, 31, 42, 46, 44, 55];
  const w = 300;
  const h = 80;
  const max = Math.max(...points);
  const step = w / (points.length - 1);

  const path = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(1)} ${(h - (p / max) * (h - 8)).toFixed(1)}`)
    .join(' ');

  return (
    <div className="rounded-2xl bg-slate-950/90 border border-slate-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-bold text-slate-200">{title}</span>
        <span className="tag-mono text-[10px] uppercase tracking-widest text-slate-600">
          приклад
        </span>
      </div>

      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent-rgb))" stopOpacity="0.28" />
            <stop offset="100%" stopColor="rgb(var(--accent-rgb))" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path d={`${path} L ${w} ${h} L 0 ${h} Z`} fill="url(#spark-fill)" />
        <path
          d={path}
          fill="none"
          stroke="rgb(var(--accent-rgb))"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
};

/* ──────────────────────── Схеми стратегій ──────────────────────────── */

/**
 * Маленька схема під кожен режим.
 *
 * Показує саме те, чим режими відрізняються механічно: спред — це коло
 * між двома майданчиками, тейкер — вхід у чужу склянку з одного боку,
 * мейкер — власне оголошення перед стінкою конкурента.
 */
export const StrategyDiagram: React.FC<{ kind: 'spread' | 'taker' | 'maker' }> = ({ kind }) => {
  const stroke = 'rgb(var(--accent-rgb))';
  const dim = 'rgb(148 163 184 / 0.35)';

  // Три схеми живуть на одній сторінці, а id в SVG — глобальні: без
  // унікального префікса всі стрілки посилались би на маркери першої.
  const uid = React.useId().replace(/:/g, '');
  const ar = `ar-${uid}`;
  const arDim = `ard-${uid}`;

  return (
    <svg viewBox="0 0 200 90" className="w-full h-[90px]" aria-hidden>
      {kind === 'spread' && (
        <>
          <rect x="8" y="30" width="52" height="30" rx="6" fill="none" stroke={dim} />
          <text x="34" y="49" textAnchor="middle" fill="#94a3b8" fontSize="11" fontFamily="monospace">
            біржа A
          </text>

          <rect x="140" y="30" width="52" height="30" rx="6" fill="none" stroke={dim} />
          <text x="166" y="49" textAnchor="middle" fill="#94a3b8" fontSize="11" fontFamily="monospace">
            біржа B
          </text>

          <path d="M62 38 H138" stroke={stroke} strokeWidth="1.5" markerEnd={`url(#${ar})`} />
          <path d="M138 54 H62" stroke={dim} strokeWidth="1.5" markerEnd={`url(#${arDim})`} />
          <text x="100" y="30" textAnchor="middle" fill={stroke} fontSize="10" fontFamily="monospace">
            купівля
          </text>
          <text x="100" y="74" textAnchor="middle" fill="#64748b" fontSize="10" fontFamily="monospace">
            продаж
          </text>
        </>
      )}

      {kind === 'taker' && (
        <>
          {/* Склянка: рівні різної довжини, один із них — цільовий */}
          {[0, 1, 2, 3, 4].map(i => (
            <rect
              key={i}
              x="20"
              y={12 + i * 14}
              width={40 + i * 22}
              height="8"
              rx="2"
              fill={i === 2 ? stroke : dim}
              opacity={i === 2 ? 0.9 : 0.5}
            />
          ))}
          <path d="M186 44 H130" stroke={stroke} strokeWidth="1.5" markerEnd={`url(#${ar})`} />
          <text x="158" y="34" textAnchor="middle" fill={stroke} fontSize="10" fontFamily="monospace">
            твій вхід
          </text>
        </>
      )}

      {kind === 'maker' && (
        <>
          {[0, 1, 2, 3, 4].map(i => (
            <rect
              key={i}
              x="20"
              y={12 + i * 14}
              width={i === 3 ? 120 : 40 + i * 12}
              height="8"
              rx="2"
              fill={dim}
              opacity={i === 3 ? 0.75 : 0.4}
            />
          ))}
          {/* Твоє оголошення стає перед стінкою, а не за нею */}
          <rect x="20" y="40" width="52" height="8" rx="2" fill={stroke} />
          <path d="M150 44 H80" stroke={stroke} strokeWidth="1.5" strokeDasharray="3 3" markerEnd={`url(#${ar})`} />
          <text x="150" y="34" textAnchor="end" fill="#64748b" fontSize="10" fontFamily="monospace">
            стінка
          </text>
        </>
      )}

      <defs>
        <marker id={ar} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 z" fill={stroke} />
        </marker>
        <marker id={arDim} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 z" fill={dim} />
        </marker>
      </defs>
    </svg>
  );
};
