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
}> = ({ children, className }) => {
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
    <div ref={ref} className={cn('glow-card', className)}>
      {children}
    </div>
  );
};
