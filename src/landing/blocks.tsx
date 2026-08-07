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

/* ────────────────────────── Спектр ризику ──────────────────────────── */

/**
 * Смуги вердикту однією шкалою.
 *
 * Раніше це були чотири однакові картки в ряд, і з них не читалось
 * головне: смуги неоднакові за шириною. OK займає п'яту частину шкали,
 * BLOCK — чверть. На градієнті це видно без жодної цифри.
 *
 * Межі — з CompositeScorer.to_verdict: 20, 45, 75.
 */
export const RiskSpectrum: React.FC<{ marker?: number }> = ({ marker }) => {
  const bands = [
    { name: 'OK', from: 0, to: 20, color: 'rgb(16 185 129)' },
    { name: 'WARN', from: 20, to: 45, color: 'rgb(234 179 8)' },
    { name: 'SUSPICIOUS', from: 45, to: 75, color: 'rgb(249 115 22)' },
    { name: 'BLOCK', from: 75, to: 100, color: 'rgb(239 68 68)' },
  ];

  return (
    <div>
      <div className="relative flex h-2.5 rounded-full overflow-hidden gap-px mb-3">
        {bands.map(b => (
          <span
            key={b.name}
            style={{ width: `${b.to - b.from}%`, background: b.color }}
            aria-hidden
          />
        ))}

        {marker !== undefined && (
          <span
            className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-1 h-5 rounded-full bg-white shadow-lg"
            style={{ left: `${marker}%` }}
            aria-hidden
          />
        )}
      </div>

      {/* Підписи стоять на своїх межах, а не рівномірно — інакше шкала бреше */}
      <div className="relative h-8">
        {bands.map(b => (
          <div
            key={b.name}
            className="absolute top-0 flex flex-col"
            style={{ left: `${b.from}%` }}
          >
            <span className="tag-mono text-[10px] font-bold" style={{ color: b.color }}>
              {b.name}
            </span>
            <span className="tag-mono text-[10px] text-slate-600 tabular-nums">
              {b.from}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

/* ─────────────────────── Смуга сканування бірж ─────────────────────── */

/**
 * Майданчики з бігучим підсвіченням.
 *
 * Показує те, чого не скажеш списком: біржі опитуються не по черзі, а
 * разом, і будь-яка може випасти в cooldown, не зупиняючи решту.
 */
export const ScanStrip: React.FC<{ items: string[] }> = ({ items }) => (
  <div className="flex flex-wrap gap-2">
    {items.map((name, i) => (
      <span
        key={name}
        className="tag-mono relative text-[11px] px-2.5 py-1.5 rounded-lg bg-slate-950/80 border border-slate-800 text-slate-400 overflow-hidden"
      >
        <span
          className="absolute inset-0 bg-accent-500/15 animate-scan-sweep"
          style={{ animationDelay: `${i * 0.18}s` }}
          aria-hidden
        />
        <span className="relative">{name}</span>
      </span>
    ))}
  </div>
);

/* ────────────────────────── Лійка фільтрів ─────────────────────────── */

/**
 * Скільки лишається на кожному етапі.
 *
 * Найкоротший спосіб пояснити, навіщо взагалі фільтри: з тисячі
 * оголошень до тебе доходять одиниці. Числа ілюстративні й підписані.
 */
export const FilterFunnel: React.FC<{
  steps: { label: string; value: number }[];
}> = ({ steps }) => {
  const max = steps[0]?.value || 1;

  return (
    <div className="space-y-2.5">
      {steps.map((s, i) => (
        <div key={s.label} className="flex items-center gap-3">
          <span className="text-[11px] text-slate-500 w-28 shrink-0 truncate">{s.label}</span>

          <div className="flex-1 h-6 rounded bg-slate-950/80 overflow-hidden min-w-0">
            <div
              className="h-full rounded transition-all duration-700"
              style={{
                width: `${Math.max(4, (s.value / max) * 100)}%`,
                // Що далі по лійці, то яскравіше: лишається найцінніше
                background: `rgb(var(--accent-rgb) / ${0.18 + i * 0.2})`,
              }}
            />
          </div>

          <span className="tag-mono text-xs font-bold text-slate-200 tabular-nums w-10 text-right shrink-0">
            {s.value}
          </span>
        </div>
      ))}
    </div>
  );
};

/* ─────────────────────────── Смуги лімітів ─────────────────────────── */

/** Заповненість добового ліміту по банках — те, що сканер звіряє щоразу. */
export const LimitBars: React.FC<{
  rows: { bank: string; used: number; limit: number }[];
}> = ({ rows }) => (
  <div className="space-y-3">
    {rows.map(r => {
      const pct = Math.min(100, (r.used / r.limit) * 100);
      // Ближче до стелі — тепліший колір: далі обсяг ріжеться
      const tone = pct > 85 ? 'bg-red-500' : pct > 60 ? 'bg-orange-500' : 'bg-accent-500';

      return (
        <div key={r.bank}>
          <div className="flex items-baseline justify-between mb-1.5">
            <span className="text-xs text-slate-300">{r.bank}</span>
            <span className="tag-mono text-[10px] text-slate-500 tabular-nums">
              {(r.used / 1000).toFixed(0)}k / {(r.limit / 1000).toFixed(0)}k ₴
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-950/80 overflow-hidden">
            <div className={cn('h-full rounded-full', tone)} style={{ width: `${pct}%` }} />
          </div>
        </div>
      );
    })}
  </div>
);

/* ──────────────────────── Статуси сесій бірж ───────────────────────── */

/** Де сесія жива, а де протухла — найчастіша причина, чому біржа мовчить. */
export const SessionStatus: React.FC<{
  rows: { name: string; ok: boolean; note: string }[];
}> = ({ rows }) => (
  <div className="grid sm:grid-cols-2 gap-2">
    {rows.map(r => (
      <div
        key={r.name}
        className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-slate-950/70 border border-slate-800/80"
      >
        <span
          className={cn('w-1.5 h-1.5 rounded-full shrink-0', r.ok ? 'bg-accent-500' : 'bg-orange-500')}
        />
        <span className="text-xs font-bold text-slate-200 flex-1 min-w-0 truncate">{r.name}</span>
        <span className="tag-mono text-[10px] text-slate-500 shrink-0">{r.note}</span>
      </div>
    ))}
  </div>
);

/* ────────────────────── Промінь по ряду метрик ─────────────────────── */

/**
 * Смуга світла, що проходить згори вниз крізь усе, що всередині.
 *
 * Один промінь на весь ряд, а не по одному в кожній плитці: чотири
 * незалежні смуги читались би як чотири віджети, а так це один сканер,
 * що йде по показниках — рівно те, чим займається продукт.
 *
 * Промінь лежить під вмістом, щоб не гасити цифри: він підсвічує тло й
 * дужки, а не перекриває текст.
 */
export const SweepFrame: React.FC<{
  children: React.ReactNode;
  className?: string;
}> = ({ children, className }) => (
  <div className={cn('relative overflow-hidden', className)}>
    <span className="sweep-down" aria-hidden />
    <div className="relative">{children}</div>
  </div>
);
