import { useEffect, useRef, useState } from 'react';

/**
 * Позначає, що значення щойно змінилось.
 *
 * Для інструменту, де рішення приймають за секунди, це важливіше за будь-яку
 * анімацію появи: список оновлюється раз на 5 секунд, і без підсвіту
 * зрозуміти, що ціна в наявному спреді поїхала, було неможливо — картка
 * виглядає так само, просто цифра інша.
 *
 * Повертає напрямок зміни, а не просто прапорець: зростання й падіння ціни
 * означають протилежні речі, і колір має це показувати.
 */
export type ChangeDirection = 'up' | 'down' | null;

export function useChangeFlash(value: number | undefined, durationMs = 900): ChangeDirection {
  const previous = useRef(value);
  const [direction, setDirection] = useState<ChangeDirection>(null);

  useEffect(() => {
    const before = previous.current;
    previous.current = value;

    // Перший рендер — не зміна, а поява. Підсвічувати нічого.
    if (before === undefined || value === undefined || before === value) return;

    setDirection(value > before ? 'up' : 'down');
    const timer = setTimeout(() => setDirection(null), durationMs);
    return () => clearTimeout(timer);
  }, [value, durationMs]);

  return direction;
}
