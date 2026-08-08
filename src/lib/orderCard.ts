/**
 * Що показувати в картці знайденого ордера.
 *
 * Аналог меню «🖥 Налаштування виводу» в боті, але свій: у Telegram
 * повідомлення читають одне за одним, і туди влазить довгий текст умов та
 * розбір LLM. На сайті картки стоять сіткою по 20–50 штук, тож ті самі
 * поля перетворюють список на стіну тексту, крізь яку не видно цін.
 *
 * Тому набір окремий і за замовчуванням вужчий: показуємо те, за чим
 * реально приймають рішення — ціна, ліміти, банки, статистика мерчанта й
 * ризик. Умови угоди і вік акаунта доступні, але вимкнені: вони цінні, коли
 * дивишся на конкретного мерчанта, а не гортаєш стрічку.
 */

export interface OrderCardFields {
  /** Кількість угод і відсоток завершення. */
  stats: boolean;
  /** Банки, які приймає мерчант. */
  banks: boolean;
  /** Доступний обсяг у USDT. */
  volume: boolean;
  /** Позначка верифікації біржі. */
  verified: boolean;
  /** Вік акаунта мерчанта. */
  age: boolean;
  /** Коли мерчант був онлайн. */
  online: boolean;
  /** Відсоток негативних відгуків. */
  reviews: boolean;
  /** Прапорець ризик-движка й композитний скор. */
  risk: boolean;
  /** Текст умов угоди від мерчанта. */
  terms: boolean;
  /** Кнопка копіювання ID мерчанта. */
  merchantId: boolean;
  /** Субсидія новачка. */
  subsidy: boolean;
}

export const ORDER_CARD_DEFAULTS: OrderCardFields = {
  stats: true,
  banks: true,
  volume: true,
  verified: true,
  age: false,
  online: true,
  reviews: true,
  risk: true,
  terms: false,
  merchantId: false,
  subsidy: true,
};

export const ORDER_CARD_LABELS: {
  key: keyof OrderCardFields;
  title: string;
  hint: string;
}[] = [
  { key: 'stats', title: 'Статистика мерчанта', hint: 'Кількість угод і відсоток завершення' },
  { key: 'banks', title: 'Банки', hint: 'Які банки приймає мерчант' },
  { key: 'volume', title: 'Доступний обсяг', hint: 'Скільки USDT лишилось в оголошенні' },
  { key: 'verified', title: 'Позначка верифікації', hint: 'Галочка біржі біля імені' },
  { key: 'online', title: 'Онлайн', hint: 'Коли мерчант востаннє був у мережі' },
  { key: 'reviews', title: 'Негативні відгуки', hint: 'Відсоток негативу з аналізу відгуків' },
  { key: 'risk', title: 'Ризик', hint: 'Прапорець ризик-движка й композитний скор' },
  { key: 'age', title: 'Вік акаунта', hint: 'Скільки днів акаунту мерчанта' },
  { key: 'terms', title: 'Умови угоди', hint: 'Текст оголошення — робить картку помітно вищою' },
  { key: 'merchantId', title: 'Кнопка «ID»', hint: 'Копіювання ID мерчанта — для скарг і банів' },
  { key: 'subsidy', title: 'Субсидія новачка', hint: 'Позначка промо-оголошення' },
];

/** Налаштування з дефолтами для полів, яких ще немає в збереженому наборі. */
export function resolveOrderCardFields(saved?: Partial<OrderCardFields>): OrderCardFields {
  return { ...ORDER_CARD_DEFAULTS, ...(saved ?? {}) };
}

/** Скільки блоків усередині картки увімкнено — для підказки про щільність. */
export function countHeavyFields(fields: OrderCardFields): number {
  return [fields.volume, fields.age, fields.online, fields.reviews].filter(Boolean).length;
}
