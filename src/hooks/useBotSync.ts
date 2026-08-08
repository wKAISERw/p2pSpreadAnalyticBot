import { useEffect } from 'react';
import { useSWRConfig } from 'swr';
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
 * перезавантаження сторінки. Саме це тут і лікується: раз на інтервал
 * помічені розділи перечитуються.
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

  // Список у залежностях має бути стабільним рядком — інакше кожен рендер
  // перезапускав би таймер і оновлення не спрацьовувало б ніколи.
  const enabledSections = (Object.keys(sync.sections) as SyncSectionKey[])
    .filter(section => sync.sections[section])
    .sort()
    .join(',');

  useEffect(() => {
    if (!active || !sync.enabled || !enabledSections) return;

    const prefixes = new Set(
      enabledSections.split(',').flatMap(section => SECTION_KEYS[section as SyncSectionKey] ?? [])
    );
    if (!prefixes.size) return;

    const interval = setInterval(() => {
      // Ревалідуємо без оптимістичних даних: хай SWR просто сходить на
      // бекенд і оновить те, що змінилось.
      mutate(key => matches(key, prefixes), undefined, { revalidate: true });
    }, Math.max(5, sync.intervalSeconds) * 1000);

    return () => clearInterval(interval);
  }, [active, sync.enabled, sync.intervalSeconds, enabledSections, mutate]);
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
