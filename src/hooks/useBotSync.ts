import { useEffect, useRef } from 'react';
import { useSWRConfig } from 'swr';
import { toast } from 'sonner';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { SyncSectionKey, SyncSettings } from '../types';

/**
 * Автопідхоплення змін, зроблених у Telegram-боті.
 *
 * Що тут важливо розуміти: копіювати нічого не треба. Фільтри, картки,
 * ліміти, пресети тейкер-режимів і пороги мерчанта лежать в одній базі —
 * тій самій, яку править бот. Сайт читає її напряму через /api/v1/*.
 *
 * Розходження виникало не через «дві копії даних», а через кеш SWR: панелі
 * читали свої ендпоінти один раз при відкритті і більше не переймались.
 * Змінив щось у боті — на відкритій вкладці це лишалось невидимим до
 * перезавантаження сторінки.
 *
 * Перша версія лікувала це в лоб: раз на інтервал перечитувала всі
 * увімкнені розділи. При десяти розділах і кроці 10 секунд це 60 запитів
 * на хвилину, з яких майже всі повертали ті самі дані — і кожен усе одно
 * перемальовував панель, бо SWR віддає новий об'єкт незалежно від вмісту.
 *
 * Тепер опитується один дешевий відбиток (GET /user/sync-state), а по дані
 * хук іде лише туди, де сума змінилась. Заразом стає відомо, ЩО саме
 * змінилось, — без цього повідомити людину не було б чим.
 *
 * Чому вибірково, а не «оновлювати все завжди»: панелі з чернетками
 * (Фільтри, Пороги мерчанта) тримають незбережені правки поверх серверних
 * значень. Якщо в цей момент прилетить свіжа відповідь, вона зіб'є те, що
 * людина щойно набрала. Тому кожен розділ можна лишити на ручному режимі.
 */

/**
 * Які SWR-ключі належать якому розділу.
 *
 * Ключі мають збігатися з тими, що передані в useSWR у панелях; масив у
 * ключі означає «ключ із параметром» (напр. ['/cards', telegramId]).
 */
/*
 * Розділ = свій набір ендпоінтів.
 *
 * Спершу тут було злито «фільтри + пресети + пороги мерчанта» в один
 * перемикач: усі троє приходили одним GET /user/filters, і роздільні
 * галочки були б декорацією. Бекенд тепер віддає групи окремо
 * (/user/filters/core і /user/filters/taker), тож перемикачі стали
 * справжніми.
 *
 * `/user/filters` лишається у filters: повний набір читають панелі, яким
 * потрібно все одразу, і без нього вимкнена група однаково оновлювалась би.
 */
const SECTION_KEYS: Record<SyncSectionKey, string[]> = {
  filters: ['/user/filters', '/user/filters/core', '/banks'],
  taker: ['/user/filters/taker'],
  merchant: ['/user/merchant-filters', '/user/subsidies'],
  cards: ['/cards', '/cards/report', '/mono-tracker', '/cards/transactions'],
  limits: ['/user/bank-limits'],
  display: ['/user/display', '/user/card-display'],
  sniper: ['/user/sniper'],
  features: ['/user/features'],
  blacklist: ['/blacklist'],
  exchanges: ['/telegram/sync', '/exchanges', '/monitoring/sessions'],
};

export const SYNC_SECTION_LABELS: Record<SyncSectionKey, { title: string; hint: string }> = {
  filters: {
    title: 'Фільтри',
    hint: 'Капітал, спред, банки, режим сканера',
  },
  taker: {
    title: 'Пресети тейкера',
    hint: 'Обсяг, ціна, швидкість і стратегія для Taker Buy / Taker Sell',
  },
  merchant: {
    title: 'Пороги мерчантів',
    hint: 'Ордери, рейтинг, вік акаунта, субсидії новачків',
  },
  cards: { title: 'Картки', hint: 'Баланси, транзакції, звіт, Mono-трекер' },
  limits: { title: 'Ліміти банків', hint: 'Спільні ліміти для карток кожного банку' },
  display: { title: 'Вивід алертів', hint: 'Що показувати в повідомленнях, паузи, картковий модуль' },
  sniper: { title: 'Снайпер-правила', hint: 'Правила, що пробивають беззвучний режим' },
  features: { title: 'Експериментальні функції', hint: 'Перемикачі з меню /features' },
  blacklist: { title: 'Чорний список', hint: 'Твої й спільні бани' },
  exchanges: { title: 'Біржі', hint: 'Підключені ключі, доступність, свіжість сесій' },
};

export const DEFAULT_SYNC: SyncSettings = {
  enabled: true,
  intervalSeconds: 30,
  notify: true,
  sections: {
    filters: true,
    taker: true,
    merchant: true,
    cards: true,
    limits: true,
    display: true,
    sniper: true,
    features: true,
    blacklist: true,
    exchanges: true,
  },
};

/** Налаштування синхронізації з дефолтами для полів, яких ще немає. */
export function resolveSync(sync?: SyncSettings): SyncSettings {
  if (!sync) return DEFAULT_SYNC;
  return {
    enabled: sync.enabled ?? DEFAULT_SYNC.enabled,
    intervalSeconds: sync.intervalSeconds || DEFAULT_SYNC.intervalSeconds,
    sections: { ...DEFAULT_SYNC.sections, ...(sync.sections ?? {}) },
    notify: sync.notify ?? DEFAULT_SYNC.notify,
  };
}

/** Чи належить SWR-ключ увімкненому розділу. */
function matches(key: unknown, prefixes: Set<string>): boolean {
  const asString = Array.isArray(key) ? String(key[0]) : String(key);
  return prefixes.has(asString);
}

export function useBotSync(active: boolean) {
  const { mutate } = useSWRConfig();
  const sync = resolveSync(useAppStore(state => state.userSettings.sync));

  // Відбитки з минулого тіку. У ref, а не в стані: їх зміна не повинна
  // перемальовувати нічого — вони лише привід сходити по дані.
  const seen = useRef<Partial<Record<SyncSectionKey, string | null>>>({});
  // Перший тік лише запам'ятовує відбитки. Інакше кожне відкриття вкладки
  // рапортувало б про «зміни» у всіх десяти розділах одразу.
  const primed = useRef(false);

  // Список у залежностях має бути стабільним рядком — інакше кожен рендер
  // перезапускав би таймер і оновлення не спрацьовувало б ніколи.
  const enabledSections = (Object.keys(sync.sections) as SyncSectionKey[])
    .filter(section => sync.sections[section])
    .sort()
    .join(',');

  useEffect(() => {
    if (!active || !sync.enabled || !enabledSections) {
      primed.current = false;
      return;
    }

    const enabled = enabledSections.split(',') as SyncSectionKey[];
    let stopped = false;

    const tick = async () => {
      let state: Awaited<ReturnType<typeof api.getSyncState>>;
      try {
        state = await api.getSyncState();
      } catch {
        // Бекенд не відповів — це не привід ані оновлювати, ані шуміти.
        return;
      }
      if (stopped) return;

      const fresh = state?.sections ?? {};
      const changed: SyncSectionKey[] = [];

      for (const section of enabled) {
        const now = fresh[section];
        // null означає «бекенд не зміг порахувати». Вважати це зміною не
        // можна: розділ оновлювався б на кожному тіку, тобто рівно так,
        // як до цієї переробки.
        if (now == null) continue;
        if (seen.current[section] !== now) changed.push(section);
        seen.current[section] = now;
      }

      if (!primed.current) {
        primed.current = true;
        return;
      }
      if (!changed.length) return;

      const prefixes = new Set(changed.flatMap(s => SECTION_KEYS[s] ?? []));
      await mutate(key => matches(key, prefixes), undefined, { revalidate: true });

      if (sync.notify !== false) notifyChanged(changed);
    };

    void tick();
    const interval = setInterval(tick, Math.max(5, sync.intervalSeconds) * 1000);

    return () => {
      stopped = true;
      clearInterval(interval);
    };
  }, [active, sync.enabled, sync.intervalSeconds, sync.notify, enabledSections, mutate]);
}

/**
 * Повідомлення про те, що змінилось.
 *
 * Перелік розділів, без конкретики: показати «капітал 5100 → 7000» можна
 * лише порівнявши старі дані з новими, а їх на цей момент уже перезаписано.
 * Та й при кількох правках підряд така стрічка перетворюється на журнал,
 * якого ніхто не читає. Назва розділу відповідає на єдине питання, яке тут
 * справді стоїть: куди подивитись.
 */
function notifyChanged(sections: SyncSectionKey[]) {
  const titles = sections.map(s => SYNC_SECTION_LABELS[s]?.title ?? s);

  if (titles.length === 1) {
    toast.info(`Оновлено з бота: ${titles[0]}`);
    return;
  }
  toast.info('Оновлено з бота', {
    description: titles.join(' · '),
  });
}

/** Разове перечитування всіх увімкнених розділів — кнопка «Оновити зараз». */
export function syncNow(sync: SyncSettings, mutate: ReturnType<typeof useSWRConfig>['mutate']) {
  const prefixes = new Set(
    (Object.keys(sync.sections) as SyncSectionKey[])
      .filter(section => sync.sections[section])
      .flatMap(section => SECTION_KEYS[section] ?? [])
  );
  if (!prefixes.size) return Promise.resolve([]);
  return mutate(key => matches(key, prefixes), undefined, { revalidate: true });
}
