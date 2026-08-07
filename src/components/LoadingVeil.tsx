import React, { useEffect, useState } from 'react';

/**
 * Індикатор для того, що справді задумалось.
 *
 * Ключове рішення — затримка перед показом. Роутові чанки здебільшого
 * приїжджають за десятки мілісекунд, і завантажувач, який блимнув і
 * зник, читається як глюк, а не як «зачекай». Тому перші 220 мс не
 * показуємо нічого: якщо встигли — людина не побачить переходу взагалі,
 * а якщо ні — з'явиться пояснення, чому екран порожній.
 *
 * Вигляд навмисно той самий, що в завантажувача в index.html: людина
 * бачила його при першому відкритті, і повторення читається як «той
 * самий продукт думає», а не як чужий спінер.
 */

const REVEAL_DELAY_MS = 220;

export default function LoadingVeil({
  label = 'Завантаження',
  compact = false,
}: {
  label?: string;
  /** Для панелей усередині дашборду: не займає весь екран. */
  compact?: boolean;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setVisible(true), REVEAL_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div
      className={cnv(
        'flex flex-col items-center justify-center gap-6',
        compact ? 'min-h-[320px]' : 'min-h-[70vh]'
      )}
      aria-busy="true"
      aria-live="polite"
      aria-label={label}
    >
      <div
        className={cnv(
          'flex flex-col items-center gap-5 transition-opacity duration-300',
          visible ? 'opacity-100' : 'opacity-0'
        )}
      >
        {/* Той самий знак, що в бут-лоадері: пульс на смарагдовому квадраті */}
        <span
          className={cnv(
            'relative rounded-2xl bg-accent-500 flex items-center justify-center animate-boot-pulse',
            compact ? 'w-12 h-12' : 'w-16 h-16'
          )}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="rgb(2 6 23)"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={compact ? 'w-6 h-6' : 'w-8 h-8'}
            aria-hidden
          >
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        </span>

        <span className="tag-mono text-[11px] uppercase tracking-[0.25em] text-slate-500">
          {label}
        </span>
      </div>
    </div>
  );
}

/** Локальний cn, щоб компонент не тягнув залежностей на критичному шляху. */
function cnv(...parts: (string | false | undefined)[]) {
  return parts.filter(Boolean).join(' ');
}
