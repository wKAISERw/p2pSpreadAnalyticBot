import React, { useEffect, useRef, useState } from 'react';
import { cn } from '../lib/utils';

/**
 * Рух для публічних сторінок.
 *
 * Свідомо на IntersectionObserver і CSS замість бібліотеки анімацій:
 * лендинг — перше, що бачить людина, і тягнути сюди рантайм заради появи
 * блоків означало б платити ~40 кБ за ефект, який робиться класом.
 */

/**
 * Чи вміє браузер прив'язувати анімацію до прокрутки.
 *
 * Якщо вміє — появу веде CSS (animation-timeline: view()), і обсервер не
 * потрібен зовсім: він робив би ту саму роботу гірше, бо дає одноразовий
 * перемикач замість плавного прогресу.
 */
const HAS_VIEW_TIMELINE =
  typeof CSS !== 'undefined' &&
  typeof CSS.supports === 'function' &&
  CSS.supports('animation-timeline: view()');

/** Спільний обсервер на всі елементи — по одному на кожен це зайві витрати. */
let observer: IntersectionObserver | null = null;
const callbacks = new WeakMap<Element, () => void>();

function getObserver(): IntersectionObserver | null {
  if (typeof IntersectionObserver === 'undefined') return null;

  if (!observer) {
    observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          callbacks.get(entry.target)?.();
          // Поява одноразова: повторний програш при скролі вгору-вниз
          // перетворює сторінку на блимання.
          observer!.unobserve(entry.target);
          callbacks.delete(entry.target);
        }
      },
      { rootMargin: '0px 0px -12% 0px', threshold: 0.05 }
    );
  }
  return observer;
}

export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  // Під CSS-таймлайном початковий стан не має значення: анімація
  // перебиває його. Ставимо true, щоб без JS нічого не лишилось прихованим.
  const [shown, setShown] = useState(HAS_VIEW_TIMELINE);

  useEffect(() => {
    // Сучасний браузер веде появу сам — не спостерігаємо взагалі.
    if (HAS_VIEW_TIMELINE) return;

    const node = ref.current;
    if (!node) return;

    const io = getObserver();
    if (!io) {
      // Без обсервера (старий браузер) показуємо одразу, а не ховаємо назавжди.
      setShown(true);
      return;
    }

    // Елемент, що вже у в'юпорті на момент монтування, має з'явитись
    // без чекання скролу.
    if (node.getBoundingClientRect().top < window.innerHeight) {
      setShown(true);
      return;
    }

    callbacks.set(node, () => setShown(true));
    io.observe(node);

    return () => {
      io.unobserve(node);
      callbacks.delete(node);
    };
  }, []);

  return { ref, shown };
}

/** Обгортка: з'являється, коли доскролили. delay — для каскаду в рядку. */
export const Reveal: React.FC<{
  children: React.ReactNode;
  className?: string;
  delay?: number;
  as?: 'div' | 'section' | 'li';
}> = ({ children, className, delay = 0, as: Tag = 'div' }) => {
  const { ref, shown } = useReveal<HTMLDivElement>();

  return (
    <Tag
      ref={ref as never}
      className={cn('reveal', shown && 'reveal-in', className)}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </Tag>
  );
};

/**
 * Картка зі світінням, що йде за курсором.
 *
 * Координати пишемо в CSS-змінні напряму на вузлі: це не викликає
 * ререндер React і не тримає стан на кожен рух миші. На тач-пристроях
 * слухач не вішається взагалі — там немає курсора, а pointermove на
 * скролі коштував би дарма.
 */
export const GlowCard: React.FC<{
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}> = ({ children, className, style }) => {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;

    const onMove = (e: PointerEvent) => {
      const rect = node.getBoundingClientRect();
      node.style.setProperty('--mx', `${e.clientX - rect.left}px`);
      node.style.setProperty('--my', `${e.clientY - rect.top}px`);
    };

    node.addEventListener('pointermove', onMove);
    return () => node.removeEventListener('pointermove', onMove);
  }, []);

  return (
    <div ref={ref} className={cn('glow-card', className)} style={style}>
      {children}
    </div>
  );
};

/* ─────────────────── Малювання лінії за прокруткою ─────────────────── */

/**
 * Заповнює відрізок лінії залежно від того, де він зараз відносно вікна.
 *
 * Прогрес пишеться прямо в стиль вузла, без стану React: інакше кожен
 * кадр прокрутки давав би ререндер компонента, а їх на сторінці п'ять.
 *
 * Якірна лінія — трохи нижче середини екрана. Так заповнення йде трохи
 * попереду очей: коли читаєш крок, лінія до нього вже дійшла.
 *
 * Слухач свій на кожен виклик, а не спільний реєстр на всіх. Спільний
 * тут уже був: він додавав слухача, коли набір підписок порожній, і
 * знімав, коли він порожніє знову. Під подвійним монтуванням у
 * StrictMode ця пара розліталась — підписки лишались, а слухача на вікні
 * вже не було, і лінія завмирала на значенні, порахованому при монтуванні.
 * Кілька пасивних слухачів із власним rAF коштують незмірно менше, ніж
 * така крихкість.
 */
export function useScrollDraw<Track extends HTMLElement>() {
  const trackRef = useRef<Track>(null);
  const fillRef = useRef<HTMLElement>(null);
  const litRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const apply = (value: number) => {
      if (fillRef.current) fillRef.current.style.transform = `scaleY(${value})`;
      if (litRef.current) litRef.current.style.opacity = value > 0.02 ? '1' : '0';
    };

    // За вимкненого руху лінія просто намальована: прокрутка не має бути
    // умовою побачити зміст.
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      apply(1);
      return;
    }

    let frame = 0;

    const measure = () => {
      frame = 0;
      const rect = track.getBoundingClientRect();
      if (!rect.height) return;
      const anchor = window.innerHeight * 0.62;
      apply(Math.min(1, Math.max(0, (anchor - rect.top) / rect.height)));
    };

    // Подій прокрутки більше, ніж кадрів: без цієї заслінки ми міряли б
    // геометрію по кілька разів на кадр і самі собі влаштували layout thrash.
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };

    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule, { passive: true });
    measure();

    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
    };
  }, []);

  return { trackRef, fillRef, litRef };
}
