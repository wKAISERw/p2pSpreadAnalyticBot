import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { cn } from '../lib/utils';

/**
 * Рух для публічних сторінок.
 *
 * Свідомо на IntersectionObserver і CSS замість бібліотеки анімацій:
 * лендинг — перше, що бачить людина, і тягнути сюди рантайм заради появи
 * блоків означало б платити ~40 кБ за ефект, який робиться класом.
 */

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

/**
 * Скільки чекати на обсервер, перш ніж показати блок усе одно.
 *
 * Обсервер може не спрацювати з причин, які до сторінки не мають
 * стосунку: вкладка не малюється, браузер притримав колбеки, розкладка
 * змінилась після монтування. Без цієї стелі такий блок лишався б
 * невидимим до кінця сесії.
 */
const REVEAL_FAILSAFE_MS = 4000;

/**
 * Поява блока, коли до нього доскролили.
 *
 * Ключове рішення: за замовчуванням блок ВИДИМИЙ, і лише те, чим хук
 * реально береться керувати, ховається перед першим кадром. Раніше було
 * навпаки — `.reveal` мав opacity: 0 у базі, а показувався класом. Це
 * означало, що будь-який збій механізму появи робив контент назавжди
 * невидимим, і збій цей мовчазний: сторінка просто порожня.
 *
 * До того ж хук поступався дорогою CSS-таймлайну — якщо браузер
 * підтримує animation-timeline: view(), обсервер не вішався взагалі.
 * Тобто в тому самому Chrome працювала лише CSS-гілка, і якщо вже вона
 * чомусь не спрацьовувала, запасного шляху не лишалось. Тепер шлях один
 * для всіх, зі стелею за часом на додачу.
 */
export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [shown, setShown] = useState(true);

  // Саме layout-ефект: ховати треба до першого кадру, інакше блок
  // встигне блимнути видимим і одразу зникнути.
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;

    const io = getObserver();
    // Без обсервера нічого не ховаємо — хай буде без анімації, ніж ніяк.
    if (!io) return;

    /*
     * Геометрію тут більше не міряємо.
     *
     * Був виклик getBoundingClientRect() у layout-ефекті — по одному на
     * кожен блок, а їх на головній за тридцять. Кожен змушує браузер
     * порахувати розкладку негайно, і Lighthouse показував 75 мс
     * примусового перекомпонування саме тут.
     *
     * Тепер рішення приймає сам обсервер: він знає перетин без нашого
     * запиту й повідомляє асинхронно. Блок, який уже в кадрі, отримає
     * колбек одразу й лишиться видимим; той, що нижче згину, сховається
     * на кадр пізніше — але його однаково ніхто не бачить, бо він за
     * межами екрана.
     */
    callbacks.set(node, () => setShown(true));
    io.observe(node);

    // Ховаємо в наступному кадрі, щоб не тримати блок прихованим до
    // першого колбека обсервера.
    const arm = requestAnimationFrame(() => {
      if (!callbacks.has(node)) return;
      setShown(false);
    });

    const failsafe = window.setTimeout(() => setShown(true), REVEAL_FAILSAFE_MS);

    return () => {
      cancelAnimationFrame(arm);
      window.clearTimeout(failsafe);
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
  /** Зовнішній ref: картці буває треба, щоб на неї писали ще й ззовні. */
  ref?: React.Ref<HTMLElement>;
}> = ({ children, className, style, ref: outerRef }) => {
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
    <div
      // Фігурні дужки навмисно: у React 19 значення, повернуте з
      // ref-колбека, трактується як функція очищення, тож повертати
      // результат присвоєння не можна.
      ref={node => {
        ref.current = node;
        if (typeof outerRef === 'function') outerRef(node);
        else if (outerRef) (outerRef as React.RefObject<HTMLElement | null>).current = node;
      }}
      className={cn('glow-card', className)}
      style={style}
    >
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
  const cardRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const apply = (value: number) => {
      if (fillRef.current) fillRef.current.style.transform = `scaleY(${value})`;
      if (litRef.current) litRef.current.style.opacity = value > 0.02 ? '1' : '0';
      // Картка розгоряється разом із лінією й з того самого числа —
      // тож вони не можуть розійтися між собою.
      if (cardRef.current) cardRef.current.style.setProperty('--lit', value.toFixed(3));
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

  return { trackRef, fillRef, litRef, cardRef };
}
