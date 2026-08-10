// Типи фронтенду. Дзеркалять те, що реально віддає FastAPI-бекенд бота
// (api/routers/dashboard.py). Бекенд проганяє всі відповіді через
// dict_to_camel, тому тут скрізь camelCase.

import type { OrderCardFields } from './lib/orderCard';

export interface Order {
  id: string;
  price: number;
  availableAmount: number;
  minLimit: number;
  maxLimit: number;
  merchantId: string;
  merchantName: string;
  orderCount: number;
  finishRate: number;
  exchange: string;
  link: string;
  bankCodes: string[];
  /** Ті самі банки, але з назвами: мапу код→назва тримає бек. */
  banks?: BankRef[];
  riskScore?: number;
  riskFlag?: string;
  isVerified?: boolean;
  // Сканер віддає це поле з березня, у типі його не було.
  lastOnlineMins?: number | null;
  tradeTerms?: string;
  /** Чому умов не видно — див. TakerOrder.termsStatus. */
  termsStatus?: string;
  /** Висновок моделі — лежить окремо від riskFlag, у merchant_verdict. */
  ai?: AiVerdict | null;
}

/**
 * Банк ордера з уже розв'язаною назвою.
 *
 * Одному банку відповідає кілька кодів біржі («43» і «1» — Monobank), і
 * мапа для цього одна — на беку. `known: false` означає, що коду немає в
 * реєстрі бота: під нього картка не підбереться, скільки б їх не завести.
 */
export interface BankRef {
  code: string;
  slug: string;
  name: string;
  known: boolean;
}

/**
 * Те, що модель сказала про мерчанта.
 *
 * Живе не в `riskFlag`, а в таблиці вердиктів, тому на сайті довго не було
 * ні вижимки умов, ні пояснення для «безпечних» ордерів — прапорець у них
 * порожній, і показувати без цього блоку було нічого.
 */
export interface AiVerdict {
  recommendation: 'APPROVE' | 'CONDITIONAL' | 'REJECT' | 'PENDING' | 'RECHECKING' | string;
  recommendationLabel: string;
  verdict: string;
  /** Чому саме такий вердикт. */
  reason: string;
  /** Вижимка умов оголошення — мерчанти пишуть їх абзацами. */
  termsSummary: string;
  reviewsAnalysis: string;
}

export interface ArbitrageOpportunity {
  // Стабільний ключ для React — сканер формує його як "{buyId}-{sellId}".
  id: string;
  buyOrder: Order;
  sellOrder: Order;
  buyBank: string;
  sellBank: string;
  routeType: string;
  netProfit: number;
  netSpread: number;
  dealAmount: number;
  timestamp: number;
  // Ці три поля сканер НЕ віддає у /opportunities — лишаємо опційними,
  // щоб UI не малював NaN там, де даних просто немає.
  actualEntryUah?: number;
  grossSpreadPct?: number;
  totalFee?: number;
  /** null — обидві ноги на одній біржі, везти нема куди. */
  transfer?: TransferLeg | null;
  /** Розбивка комісій, які вже враховані в netSpread. */
  fees?: { label: string; amountUah: number }[];
  totalFeeUah?: number;
}

/**
 * Крок між ногами зв'язки: чим везти USDT на біржу продажу.
 *
 * Комісія мережі й раніше сиділа в `netSpread`, але сам крок ніде не був
 * видний — дві ноги стояли поруч так, ніби монети опиняються на другій
 * біржі самі собою.
 */
export interface TransferLeg {
  fromExchange: string;
  toExchange: string;
  /** Найдешевша спільна мережа — саме за нею пораховано netSpread. */
  network: string;
  feeUsdt: number;
  feeUah: number;
  /** Спільної мережі немає — маршрут насправді неможливий. */
  unroutable: boolean;
  /**
   * Усі спільні мережі, від найдешевшої. Дешевша не завжди бажана: можна
   * роками ходити через TRC20 і не хотіти заводити гаманець у мережі,
   * якою користуєшся раз.
   */
  options?: NetworkOption[];
}

export interface NetworkOption {
  network: string;
  feeUsdt: number;
  feeUah: number;
}

export interface ApiKeyConfig {
  key: string;
  secret: string;
  passphrase?: string;
}

export interface MerchantFilters {
  minOrders: number;
  minRate: number;
}

/**
 * Налаштування, які належать САЙТУ.
 *
 * Тут навмисно немає капіталу, банків і порогів мерчанта: усе це живе в
 * базі бота, і сайт читає його напряму через /user/filters. Локальні копії
 * `maxCapital`, `minCapital` і `banks` звідси прибрано — вони не впливали
 * ні на що, крім панелі синхронізації, яка сама їх і заповнювала.
 *
 * minSpread лишається: це поріг ПОКАЗУ на дашборді, свідомо окремий від
 * min_spread_pct, за яким бот вирішує, що взагалі шукати.
 */
export interface UserSettings {
  /** Ховає з дашборду слабші спреди. Не впливає на те, що шле бот. */
  minSpread: number;
  merchantFilters?: MerchantFilters;
  apiKeys?: Record<string, ApiKeyConfig>;
  telegramUserId?: string;
  isTelegramAdmin?: boolean;
  goalCapital?: number;
  soundEnabled?: boolean;
  soundVolume?: number;
  /** Відтінок акценту в OKLCH (0–360). Рядки лишились із часів,
   *  коли зберігалась назва кольору — resolveHue() їх розуміє. */
  accentColor?: number | string;
  /** Автопідхоплення змін, зроблених у Telegram-боті. */
  sync?: SyncSettings;
  /**
   * Які поля показувати в картці ордера на дашборді.
   *
   * Свій набір, а не той, що керує Telegram-алертами (`/user/display`):
   * у чаті повідомлення читають поодинці, а тут картки стоять сіткою, і те
   * саме наповнення перетворює список на стіну тексту. Тип — з lib/orderCard.
   */
  orderCard?: Partial<OrderCardFields>;
  /**
   * Як щільно показувати картку ордера в тейкер-режимах.
   *
   * `compact` — рядком, як у спред-нозі. `roomy` — плитками: ті самі цифри,
   * але картка вдвічі вища, і в списку з двадцяти ордерів це помітно.
   */
  orderLayout?: 'compact' | 'roomy';
}

/**
 * Які розділи сайт перечитує сам, коли їх міняють у боті.
 *
 * Це не копіювання даних: фільтри, картки, ліміти й пресети зберігаються в
 * одному місці — базі бота — і сайт бачить рівно те саме. Питання лише в
 * тому, як швидко відкрита вкладка помітить зміну. Вимкнений розділ просто
 * не оновлюється сам; це має сенс, коли ти саме редагуєш його на сайті й
 * не хочеш, щоб чернетку перебило значення з бота.
 */
export type SyncSectionKey =
  | 'filters'
  | 'taker'
  | 'merchant'
  | 'cards'
  | 'limits'
  | 'display'
  | 'sniper'
  | 'features'
  | 'blacklist'
  | 'exchanges';

export interface SyncSettings {
  /** Головний вимикач: без нього жоден розділ не оновлюється сам. */
  enabled: boolean;
  /** Як часто питати бекенд, чи змінилось хоч щось, секунди. */
  intervalSeconds: number;
  sections: Record<SyncSectionKey, boolean>;
  /** Показувати повідомлення про те, що саме змінилось. Типово так. */
  notify?: boolean;
}

/**
 * Відбитки розділів (GET /user/sync-state).
 *
 * Значення непрозорі: порівнювати можна лише з попереднім своїм. `null` —
 * бекенд не зміг порахувати, тоді розділ читається звичайним шляхом.
 */
export interface SyncState {
  sections: Partial<Record<SyncSectionKey, string | null>>;
}

/**
 * Списків два, і вони не взаємозамінні:
 *   personal — свій у кожного, впливає лише на власні алерти, правиться без
 *              жодних прав;
 *   global   — спільний, наповнюють ризик-движок і адміністратор, діє на всіх.
 */
export type BlacklistScope = 'personal' | 'global';

export interface BlacklistEntry {
  exchange: string;
  merchantId: string;
  merchantName: string;
  reason: string;
  source: string;
  addedAt: number;
  scope: BlacklistScope;
}

// ─── Глобальні налаштування ───────────────────────────────────────────────
//
// Джерело правди — таблиця bot_settings через config/runtime.py.
// ВАЖЛИВО: runtime_config зберігає всі значення як рядки (str(value)),
// тому GET /settings/global віддає рядки або null для незаданих ключів.
// Парсинг у нормальні типи робить services/api.ts.

export type RiskMode = 'STRICT' | 'WARNING' | 'RELAXED';

/** Сирий вигляд, як його віддає бекенд: рядки або null. */
export type GlobalSettingsRaw = Partial<Record<string, string | null>>;

/** Розпарсений вигляд для UI. Усі поля опційні — ключ може бути не заданий. */
export interface GlobalSettings {
  riskMode?: RiskMode;
  behaviorAlertScore?: number;
  velocitySpikePerHour?: number;
  stickyMinChain?: number;
  reviewTtlHours?: number;
  maxAlertsPerCycle?: number;
  isScannerActive?: boolean;
  requireSessions?: boolean;
  minSpreadPct?: number;
  safetyBufferPct?: number;
  showSpreadLogs?: boolean;
  blockFopTov?: boolean;
  blockBankaJar?: boolean;
  // Ваги ризик-движка. Бекенд тримає їх у ВЕРХНЬОМУ регістрі (W_REGEX),
  // а to_camel перетворює це саме на WRegex — звідси такі імена.
  WRegex?: number;
  WBehavior?: number;
  WReviewsPct?: number;
  WReviewsText?: number;
  WLlm?: number;
  WIdentity?: number;
  // Керується через ExchangeManager, а не напряму — тут лише для читання.
  disabledExchanges?: string;
}

/** Як парсити кожен ключ при читанні та як серіалізувати назад. */
export const GLOBAL_SETTING_KINDS = {
  riskMode: 'string',
  behaviorAlertScore: 'int',
  velocitySpikePerHour: 'float',
  stickyMinChain: 'int',
  reviewTtlHours: 'float',
  maxAlertsPerCycle: 'int',
  isScannerActive: 'bool',
  requireSessions: 'bool',
  minSpreadPct: 'float',
  safetyBufferPct: 'float',
  showSpreadLogs: 'bool',
  blockFopTov: 'bool',
  blockBankaJar: 'bool',
  WRegex: 'float',
  WBehavior: 'float',
  WReviewsPct: 'float',
  WReviewsText: 'float',
  WLlm: 'float',
  WIdentity: 'float',
  disabledExchanges: 'string',
} as const;

export type GlobalSettingKey = keyof typeof GLOBAL_SETTING_KINDS;

// ─── Стан системи ─────────────────────────────────────────────────────────

export interface SystemStats {
  cycles: number;
  lastCycleMs: number;
  botsDetectedToday: number;
  spreadsFoundToday: number;
  llmQueue: number;
  reviewQueue: number;
  cbStatus: Record<string, string>;
  isScannerActive: boolean;
  // З'явилось у state.py після березня.
  totalScanned: number;
  opportunitiesFound: number;
  internetConnected: boolean;
  avgSpread?: number;
}

/** GET /exchanges — ExchangeManager.get_status_all() */
export interface ExchangeStatus {
  name: string;
  enabled: boolean;
  disabledReason: string;
  cooldownRemainingH: number;
  isCooldown: boolean;
  failures: number;
}

/** POST /exchanges/health — по три спроби на біржу, тому повільно. */
export interface ExchangeHealthResult {
  exchange: string;
  ok: boolean;
  message: string;
}

// ─── Тейкер-ордери (GET /taker/orders) ────────────────────────────────────
//
// Одна сторона ринку, а не зв'язка: у тейкер-режимах ти береш чуже
// оголошення, тож другої ноги тут немає за визначенням.

export type TakerSide = 'buy' | 'sell' | 'both';

/**
 * Ціновий фільтр входу (GET/POST /user/price-range).
 *
 * Стосується лише спред-режиму: обмежує ціну КУПІВЛІ у зв'язці. Тейкер має
 * власні стратегії (takerBuyPriceStrategy / takerSellPriceStrategy), тому
 * тут його немає. Порожній mode = фільтр вимкнено.
 */
export type PriceRangeMode = '' | 'range' | 'exact' | 'max' | 'min';

/**
 * Банки в розрізі режимів (GET/POST /user/bank-scopes).
 *
 * base — спільні списки, які працюють у всіх режимах.
 * overrides — винятки для конкретного режиму; порожньо = беруться спільні.
 * resolved — що з цього вийде насправді, з позначкою, чи це виняток.
 *
 * Останнє тут головне: доти майстер Taker Buy писав свій вибір у спільне
 * buy_bank_codes, тобто мовчки міняв банки й для спред-режиму — і побачити
 * це можна було хіба що за зниклими зв'язками.
 */
export type BankSide = 'buy' | 'sell';

export interface BankScopes {
  base: Record<BankSide, string[]>;
  overrides: Partial<Record<ScannerMode, Partial<Record<BankSide, string[]>>>>;
  resolved: Record<ScannerMode, Record<BankSide, { banks: string[]; isOverride: boolean }>>;
}

export interface PriceRange {
  mode?: PriceRangeMode;
  min?: number;
  max?: number;
  value?: number;
}

export interface TakerOrder {
  id: string;
  exchange: string;
  price: number;
  availableAmount: number;
  minLimit: number;
  maxLimit: number;
  merchantId: string;
  merchantName: string;
  monthOrderCount: number;
  finishRatePct: number;
  positiveRate: number;
  isVerified: boolean;
  accountAgeDays: number;
  lastOnlineMins: number | null;
  bankCodes: string[];
  /** Звичайне веб-посилання на профіль мерчанта. */
  link: string;
  /**
   * Посилання, яке на телефоні відкриває мерчанта просто в застосунку
   * біржі (bot/deeplinks.py). Порожнє = підтвердженого маршруту немає.
   */
  appLink: string;
  riskFlag: string;
  compositeScore: number;
  reviewScore: number;
  reviewNegPct: number;
  tradeTerms: string;
  /**
   * Чому умови саме такі: OK / EMPTY / NO_SESSION / SESSION_EXPIRED /
   * FETCH_FAILED / NOT_SUPPORTED / UNKNOWN.
   *
   * Порожні умови означали дві протилежні речі — «мерчант нічого не
   * написав» і «ми не змогли дістати». Перше факт про мерчанта, друге про
   * нас, і плутати їх не можна.
   */
  termsStatus?: string;
  /** Готове пояснення, лише коли умов НЕ видно. Порожньо — пояснювати нічого. */
  termsStatusLabel?: string;
  isNewUserSubsidy: boolean;
  side: string;
  /** Банки з назвами — див. BankRef. */
  banks?: BankRef[];
  /** Висновок моделі: вердикт, пояснення, вижимка умов. */
  ai?: AiVerdict | null;
  /**
   * Комісія банку за переказ фіату під цей ордер. null — комісії немає
   * або це продаж (там фіат відправляє мерчант і платить він).
   */
  transferFee?: TransferFee | null;
}

/**
 * При спреді 0.5–1% комісія переказу здатна з'їсти весь профіт, тож
 * порівнювати ордери треба за `effectivePrice`, а не за «чистою» ціною.
 */
export interface TransferFee {
  bank: string;
  amountUah: number;
  description: string;
  /** Курс із закладеною комісією. */
  effectivePrice: number;
  /** Від якої суми пораховано — у комісій є пороги. */
  onAmountUah: number;
}

/**
 * Чому не пройшла конкретна КАРТКА (core/engine/rejection_codes.Rejection).
 * Код — для групування, reason — готовий рядок для людини з цифрами.
 */
export interface CardRejection {
  code: RejectionCode | string;
  reason: string;
  cardId: string | null;
  lastFour: string;
  bank: string;
  /** Скільки саме не вистачило, ₴. 0 — причина не про суму. */
  shortfallUah: number;
}

/**
 * Чому не пройшов ОРДЕР загалом (core/engine/taker_scanner.OrderRejection).
 * `details` — розклад по кожній картці, яку движок пробував.
 */
export interface OrderRejection {
  orderId: string;
  merchantName: string;
  exchange: string;
  bank: string;
  price: number;
  minLimit: number;
  code: RejectionCode | string;
  reason: string;
  shortfallUah: number;
  details: CardRejection[];
}

export type RejectionCode =
  | 'no_cards_for_bank' | 'no_active_cards' | 'unknown_bank_code' | 'cooldown'
  | 'insufficient_balance' | 'limits_exhausted' | 'max_tx_per_day'
  | 'cold_card' | 'night_window' | 'business_days_only'
  | 'below_min_trade' | 'below_merchant_min'
  | 'split_impossible' | 'split_disabled'
  | 'split_needs_inter_bank' | 'split_needs_more_cards'
  | 'no_crypto'
  // Спостереження: ордер пройшов, але не таким, як задумано.
  | 'volume_scaled_down';

/**
 * Коди, за якими грошей вистачає, а заважає налаштування.
 * Дія користувача тут інша, ніж «поповнити картку».
 */
export const SETTINGS_BLOCKED_CODES: RejectionCode[] = [
  'split_needs_inter_bank', 'split_needs_more_cards', 'split_disabled',
];

export interface TakerOrdersResponse {
  buy: TakerOrder[];
  sell: TakerOrder[];
  /**
   * Ордери, які ринок дав, а картки не пропустили. Порожній buy/sell сам
   * по собі не каже, ринку немає чи грошей не вистачило — це каже.
   */
  rejected: { buy: OrderRejection[]; sell: OrderRejection[] };
  /**
   * Бажана сума проти тієї, з якою реально можна зайти зараз.
   * null — бажана сума не задана або видача порожня.
   */
  budget: BuyBudget | null;
  /** false — сканер ще не завершив жодного циклу, це не помилка. */
  scanned: boolean;
}

/**
 * Введена користувачем сума більше не перезаписується — вона незмінний
 * вхід, а `effectiveUsdt` рахується під кожен ордер наживо. Поповнилась
 * картка — знову шукається повна `desiredUsdt`, без жодних дій.
 */
export interface BuyBudget {
  desiredUsdt: number;
  effectiveUsdt: number;
  /**
   * Скільки піде в одну угоду. Без кошиків це максимум в ОДНОМУ банку,
   * з кошиками — сума по всіх.
   */
  availableUah: number;
  price: number;
  /** Ефективна менша за бажану. */
  scaled: boolean;
  /** Не набирається навіть мінімальна угода. */
  blocked: boolean;
  /**
   * Банк, у якому лежить максимум. Порожній при `interBank` — маршрут іде
   * з кількох банків, і одна назва вводила б в оману.
   */
  bestBank: string;
  /** Діє міжбанківський набір (фіча `inter_bank_matching`). */
  interBank: boolean;
  /** Скільки грошей на картках узагалі — може бути більше за availableUah. */
  totalUah: number;
}

// ─── Готовність режиму (GET /taker/readiness) ─────────────────────────────

/** blocker — алертів не буде взагалі; warning — будуть, але не такі; note — до відома. */
export type ReadinessLevel = 'blocker' | 'warning' | 'note';

export interface ReadinessCheck {
  level: ReadinessLevel;
  text: string;
  hint: string;
  /** До якого режиму належить — режимів може бути кілька одночасно. */
  mode: string;
}

export interface ReadinessReport {
  mode: string;
  /** Усі тейкерські режими, які перевірялись. */
  modes: string[];
  /** Порожньо — все сходиться. */
  checks: ReadinessCheck[];
  hasBlockers: boolean;
}

// ─── Де лежить USDT (GET /inventory/usdt) ─────────────────────────────────

/**
 * Три різні відстані до угоди, а не три однакові кошики: фандинг
 * продається зараз, спот вимагає кліку всередині біржі, Earn — викупу.
 */
export interface ExchangeWallets {
  exchange: string;
  funding: number;
  spot: number;
  earn: number;
  /** false — Earn на цій біржі ми не бачимо, і нуль тут нічого не означає. */
  earnKnown: boolean;
  total: number;
}

export interface UsdtInventory {
  /** false — ключів немає або біржі не відповіли. Це не те саме, що нуль. */
  known: boolean;
  exchanges: ExchangeWallets[];
  totals: { funding?: number; spot?: number; earn?: number; total?: number };
}

// ─── Картки під угоду (GET /cards/match) ──────────────────────────────────

/**
 * Що бот шле окремим повідомленням після алерта: яка картка підходить,
 * чому решта ні, і які перекази це виправлять.
 */
export interface CardMatch {
  bank: string;
  bankName: string;
  amountUah: number;
  direction: 'buy' | 'sell';
  /** success | needs_split | no_cards | disabled | no_crypto */
  status: string;
  bestCard: Record<string, any> | null;
  splitOptions: Record<string, any>[][];
  /** Скільки картки цього банку разом можуть провести. */
  availableUah: number;
  rejections: CardRejection[];
  balances: CardBalance[];
  /** Порожньо для продажу: там фіат приходить нам, переказувати нічого. */
  transferTips: TransferTip[];
}

export interface CardBalance {
  id: string;
  bank: string;
  bankName: string;
  lastFour: string;
  label: string;
  balance: number;
  isWarmedUp: boolean;
}

export interface TransferTip {
  fromCardId: string;
  fromBank: string;
  fromLastFour: string;
  toCardId: string;
  toBank: string;
  toLastFour: string;
  amountUah: number;
}

/** GET /taker/rejections — розклад причин за N днів. */
export interface RejectionStatsRow {
  code: RejectionCode | string;
  /** Людська назва з CODE_LABELS. */
  title: string;
  hits: number;
  sharePct: number;
  avgShortfall: number;
  maxShortfall: number;
  banks: string[];
}

export interface RejectionStats {
  days: number;
  /** Лише відмови. Спостереження сюди не входять — інакше вийшло б «90% відмов» по ордерах, які прийшли. */
  total: number;
  since: string;
  codes: RejectionStatsRow[];
  /**
   * Ордер пройшов, але не таким, як задумано (напр. обсяг ужато під один
   * банк). Найцінніші дані для рішення, чи потрібні кошики між банками.
   */
  observations?: RejectionStatsRow[];
}

/**
 * GET /logs — кільцевий буфер із main.StateLogHandler.
 * Раніше ендпоінт завжди віддавав [], тому фронтенд типізував його як
 * журнал угод. Це реальний формат.
 */
export interface LogRecord {
  timestamp: number;
  level: string;
  source: string;
  message: string;
}

// ─── Детальна аналітика (GET /stats/detailed) ─────────────────────────────

export interface StatsSummary {
  totalTrades?: number;
  totalProfit?: number;
  avgProfit?: number;
  bestDay?: string;
}

export interface ProposalsSummary {
  total?: number;
  sent?: number;
  avgSpread?: number;
  avgProfit?: number;
  maxSpread?: number;
  totalPotentialProfit?: number;
}

export interface DailyPoint {
  date: string;
  profit: number;
  trades: number;
}

export interface ExchangeStat {
  exchange: string;
  volumeUah: number;
  trades: number;
}

export interface BankStat {
  bank: string;
  trades: number;
  volumeUah: number;
}

export interface PeriodStat {
  avgProfit: number;
  trades: number;
  totalProfit: number;
}

/** {"Mon": {"09": 3, "10": 5}} */
export type Heatmap = Record<string, Record<string, number>>;

export interface DetailedStats {
  summary: StatsSummary;
  proposals: ProposalsSummary;
  daily: DailyPoint[];
  exchanges: ExchangeStat[];
  banks: BankStat[];
  heatmap: Heatmap;
  weekly: Record<string, PeriodStat>;
}

/** GET /telegram/sync/{id} */
export interface TelegramSyncData {
  settings: {
    maxCapital: number;
    minSpread: number;
    banks: string[];
  };
  /** Канонічні назви підключених бірж: "OKX", "MEXC", "BingX", "Wallet". */
  keys: string[];
  isAdmin: boolean;
  error?: string;
}

// ─── Керування (api/routers/control.py) ───────────────────────────────────

export type ScannerMode = 'SPREAD' | 'MAKER_BUY' | 'MAKER_SELL' | 'TAKER_BUY' | 'TAKER_SELL';
export type SpreadStrategy = 'min' | 'max' | 'range';
export type CapitalMode = 'manual' | 'auto';

export interface MerchantThresholds {
  minOrders?: number;
  minRate?: number;
}

/**
 * GET /user/filters. Бекенд віддає не сирі колонки scanner_users, а
 * дружні імена з get_user_by_id (capital, minSpread, …) — саме їх і
 * дзеркалить цей тип.
 */
export interface UserFilters {
  userId: number;
  chatId: number;
  capital: number;
  capitalMode: CapitalMode;
  minAmount: number;
  minSpread: number;
  maxSpread: number;
  spreadStrategy: SpreadStrategy;
  bankCodes: string[];
  buyBankCodes: string[];
  sellBankCodes: string[];
  merchantFilters: MerchantThresholds;
  exchangeMerchantFilters: Record<string, MerchantThresholds>;
  /**
   * Основний режим — той, що бачить меню бота першим.
   *
   * Лишається заради старих рядків, де scannerModes ще порожній: набір
   * зʼявився пізніше, і міграції для нього немає навмисно.
   */
  scannerMode: ScannerMode;
  /** Усі активні режими. Бот сканує кожен із них одночасно. */
  scannerModes?: ScannerMode[];
  makerBuyPrice: number;
  targetMargin: number;
  isAlertsActive?: number;
  /**
   * Мережа, якою ти справді возиш USDT між біржами.
   *
   * Сканер відбирає зв'язки за найдешевшою спільною. Якщо возиш дорожчою
   * (TRC20 — 1 USDT проти 0.01 у TON), реальний спред нижчий, і поріг тепер
   * застосовується саме до нього. Порожнє — поведінка як була.
   */
  preferredNetwork?: string;
}

/**
 * Поля, які дозволено надсилати в POST /user/filters.
 * Дзеркалить _EDITABLE_FILTERS у api/routers/control.py — усе, чого немає
 * в тому словнику, бекенд поверне в списку rejected.
 * Тейкерські поля описані нижче й підмішуються сюди.
 */
export interface UserFiltersPatch extends Partial<TakerSellSettings>, Partial<TakerBuySettings> {
  workingCapital?: number;
  capitalMode?: CapitalMode;
  minAmountUah?: number;
  minSpreadPct?: number;
  maxSpreadPct?: number;
  spreadStrategy?: SpreadStrategy;
  bankCodes?: string[];
  buyBankCodes?: string[];
  sellBankCodes?: string[];
  scannerMode?: ScannerMode;
  /**
   * Набір режимів. Порожній бекенд не приймає: користувач без жодного
   * режиму нічого не отримує, а для тиші є isAlertsActive.
   */
  scannerModes?: ScannerMode[];
  isAlertsActive?: boolean;
  targetMargin?: number;
  makerBuyPrice?: number;
  /** Порожній рядок повертає поведінку «найдешевша спільна мережа». */
  preferredNetwork?: string;
}

export interface Bank {
  code: string;
  name: string;
}

// ─── Довідник банків (GET /banks/profiles) ────────────────────────────────
//
// Операційний профіль: ліміти, за якими банк не привертає уваги, комісії за
// переказ, нічні вікна, спільні ліцензії. Джерело — config/banks.py.

export interface P2PFeeProfile {
  pct: number;
  fixedUah: number;
  /** Безкоштовно, поки місячний оборот нижчий. null — порогу немає. */
  freeUntilUah: number | null;
  /** …або поки переказів за місяць менше. */
  freeTxPerMonth: number | null;
  /** Комісія тільки за переказ в інший банк. */
  crossBankOnly: boolean;
  label: string;
}

/** max_uah = null означає, що вночі перекази заборонені зовсім. */
export interface NightWindowProfile {
  fromHour: number;
  toHour: number;
  maxUah: number | null;
}

export interface BankProfile {
  /** Канонічний слаг (normalize_bank), не код біржі. */
  slug: string;
  name: string;
  /** 1 — найкраще тримає оборот, 3 — використовувати обережно. */
  tier: number;
  /** false — банк є в довіднику, але жодна біржа його не віддає. */
  tradable: boolean;
  safeMonthlyUah: number | null;
  maxMonthlyUah: number | null;
  safeTxPerDay: number | null;
  /** null — стелі на один переказ немає. */
  singleTxLimitUah: number | null;
  businessDaysOnly: boolean;
  /** Непорожнє — банк ділить ліцензію й фінмон з іншими членами групи. */
  licenseGroup: string;
  terminationFeePct: number;
  thirdPartyFriendly: boolean | null;
  note: string;
  p2pFee: P2PFeeProfile | null;
  nightWindow: NightWindowProfile | null;
  /** Що движок підставить картці цього банку без налаштувань користувача. */
  defaultLimits: Record<string, number>;
}

export interface ScannerState {
  isScannerActive: boolean;
  isMuted: boolean;
  muteSecondsLeft: number;
}

export interface CardLimits {
  [key: string]: number | string | boolean | null;
}

/** Значення збігаються з меню бота (bot/keyboards/cards.py). */
export type CardCategory = 'self' | 'relative' | 'friend' | 'drop';
export type CardStatus =
  | 'active' | 'inactive' | 'cooldown' | 'frozen' | 'frozen_funds' | 'blocked';

export interface Card {
  id: string;
  ownerId: number;
  bankName: string;
  lastFour: string;
  label: string;
  note?: string;
  category?: CardCategory;
  balance: number;
  status?: CardStatus;
  balanceUpdatedAt?: number;
  limits: CardLimits;
  usedDaily: { in: number; out: number };
  usedMonthly: { in: number; out: number };
}

export interface CardCreatePayload {
  bankName: string;
  /** 16 цифр — потрібні для звірки з випискою Monobank. */
  cardNumber?: string;
  /** Альтернатива повному номеру, коли звірка не потрібна. */
  lastFour?: string;
  label?: string;
  category?: CardCategory;
  balance?: number;
  note?: string;
}

export interface CardPatch {
  label?: string;
  note?: string;
  category?: CardCategory;
  status?: CardStatus;
  balance?: number;
}

export interface CardTransaction {
  id: string;
  cardId: string;
  amount: number;
  direction: 'in' | 'out';
  type: string;
  source: string;
  timestamp: number;
  linkedOrderId?: string | null;
}

export interface SessionStatus {
  exchange: string;
  hasSession: boolean;
  updatedAt: number | null;
  ageHours: number | null;
  isStale: boolean;
}

export interface MonitoringOrder {
  id: number;
  exchange: string;
  orderId: string;
  status: string;
  leg?: string;
  routeType?: string;
  fiatAmount?: number;
  cryptoAmount?: number;
  price?: number;
  createdAt?: string;
  expiresAt?: string;
  ownerUserId?: number | null;
}

export interface QueueStatus {
  llmQueue: number;
  reviewQueue: number;
  cbStatus: Record<string, string>;
  internetConnected: boolean;
}

// ─── Автентифікація ───────────────────────────────────────────────────────

export interface LinkedIdentity {
  provider: string;
  email?: string;
  linkedAt: number;
}

export interface AuthSession {
  token: string;
  telegramId: number;
  isAdmin: boolean;
  identities: LinkedIdentity[];
}

export interface AuthConfig {
  botUsername: string;
  widgetAvailable: boolean;
  codeAvailable: boolean;
}

/** Payload, який віддає Telegram Login Widget. */
export interface TelegramWidgetPayload {
  id: number;
  auth_date: number;
  hash: string;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
}

// ─── Налаштування виводу повідомлень ──────────────────────────────────────

export type FilterMode = 'hide' | 'show' | 'only';

export interface AutoCooldownTier {
  threshold: number;
  delay: number;
}

export interface AutoCooldown {
  windowSeconds: number;
  tiers: AutoCooldownTier[];
}

export interface DisplaySettings {
  showAiTermsSummary: boolean;
  showFullTerms: boolean;
  showAiLogic: boolean;
  showBankDetails: boolean;
  showLlmSummary: boolean;
  isHybridRoutesEnabled: boolean;
  groupActiveAlerts: boolean;
  groupScannerAlerts: boolean;
  /** -1 = використати авто-кулдаун замість фіксованої паузи. */
  alertCooldown: number;
  filterFopTov: FilterMode;
  filterBankaJar: FilterMode;
  cryptobotProfileMode: 'chat' | 'profile';
  autoCooldownJson?: AutoCooldown;
}

export type DisplaySettingsPatch = Partial<Omit<DisplaySettings, 'autoCooldownJson'>>;

// ─── Taker-режими ─────────────────────────────────────────────────────────

export type TakerSpeed = 'FAST' | 'ANY';
export type SellPriceStrategy = 'roi' | 'min' | 'range' | 'exact' | 'any';
export type BuyPriceStrategy = 'any' | 'max' | 'range' | 'exact';
export type BuyBalanceMode = 'CARD_ENFORCED' | 'AUTO_SCALE' | 'FREE';

export interface TakerSellSettings {
  takerSellAmount: number;
  takerSellPrice: number;
  takerSellExchange: string;
  takerSellProfit: number;
  takerSellMinPrice: number;
  takerSellSpeed: TakerSpeed;
  takerSellPriceStrategy: SellPriceStrategy;
  takerSellPriceTo: number;
}

export interface TakerBuySettings {
  takerBuyAmount: number;
  takerBuyMaxPrice: number;
  takerBuyLimitMin: number;
  takerBuyLimitMax: number;
  takerBuySpeed: TakerSpeed;
  takerBuyPriceStrategy: BuyPriceStrategy;
  takerBuyPriceFrom: number;
  buyBalanceMode: BuyBalanceMode;
  buyAutoScaleDown: number;
  buyAutoScaleUp: number;
}

// ─── Розширені пороги мерчантів ───────────────────────────────────────────

export type VerifiedFilter = 'all' | 'verified' | 'unverified';
/** Що робити з мерчантами з чорного списку. */
export type BlacklistMode = 'block' | 'hide' | 'show';

export interface MerchantThresholdsFull {
  minOrders?: number;
  minRate?: number;
  verifiedFilter?: VerifiedFilter;
  minAccountAgeDays?: number;
  minPositiveRate?: number;
  maxOfflineMins?: number;
  blacklistMode?: BlacklistMode;
}

// ─── Снайпер-правила ──────────────────────────────────────────────────────

/** BUY = стежимо за біржею, де продаємо; SELL = де купуємо. */
export type SniperDirection = 'BUY' | 'SELL';

export interface SniperRule {
  exchange: string;
  direction: SniperDirection;
  minSpread: number;
  minVolume: number;
}

// ─── Експериментальні фічі ────────────────────────────────────────────────

export interface FeatureFlag {
  key: string;
  name: string;
  description: string;
  enabled: boolean;
}

export interface FeatureGroup {
  key: string;
  title: string;
  features: FeatureFlag[];
}

export interface UsedSubsidy {
  exchange: string;
  used: string[];
}

// ─── Картковий модуль виводу ──────────────────────────────────────────────

export type CardOutputMode = 'inline' | 'reply';
export type CardDetailLevel = 'full' | 'compact';
export type CardModuleMode = 'off' | 'on';

/**
 * Як складати суму під ордер (config/card_limits.py).
 * `inter_bank` доступний лише коли ввімкнено експериментальну фічу
 * `inter_bank_matching` — бек інакше відхилить це значення.
 */
export type CardSplitMode = 'off' | 'intra_bank' | 'inter_bank';
export type ShowRejectedOrders = 'with_reason' | 'hide';

export interface CardDisplaySettings {
  cardModuleMode: CardModuleMode;
  cardOutputMode: CardOutputMode;
  enableSmartSpoiler: boolean;
  cardDetailLevel: CardDetailLevel;
  enableInSingleModes: boolean;
  showBalancesBreakdown: boolean;
  showTransferTips: boolean;
  coldCardLimit: number;
  /** Скільки карток максимум в одній угоді (1–3). */
  maxCardsPerOrder: number;
  cardSplitMode: CardSplitMode;
  showRejectedOrders: ShowRejectedOrders;
  /**
   * Режими, які движок уміє саме для цього користувача. Міжбанк тут
   * з'являється лише з увімкненою фічею — решту бек відхилить.
   */
  availableSplitModes: CardSplitMode[];
  /** Ключ фічі, яка розблоковує міжбанк (для посилання в «Можливості»). */
  interBankFeature: string;
}

// ─── Ліміти ───────────────────────────────────────────────────────────────

/** Поля, які приймають set_user_bank_limit і update_card_limit_override. */
export const LIMIT_FIELDS = [
  'daily_out_max', 'daily_in_max', 'monthly_out_max', 'monthly_in_max',
  'max_single_tx_out', 'max_single_tx_in', 'max_tx_per_day', 'cooldown_hours',
] as const;

export type LimitField = (typeof LIMIT_FIELDS)[number];

/**
 * Ефективні ліміти банку: дефолти довідника, перекриті тим, що задав
 * користувач. Числа тут — ті самі, за якими працює движок, тож порожніх
 * полів більше немає. Що саме задане вручну, каже `userSet`.
 */
export interface BankLimits {
  userId: number;
  bankName: string;
  dailyOutMax?: number;
  dailyInMax?: number;
  monthlyOutMax?: number;
  monthlyInMax?: number;
  maxSingleTxOut?: number;
  maxSingleTxIn?: number;
  maxTxPerDay?: number;
  cooldownHours?: number;
  /** Імена полів (snake_case), які задав користувач, а не довідник. */
  userSet?: LimitField[];
}

/** -1 у полі ліміту означає «не обмежувати» (config.banks.UNLIMITED). */
export const UNLIMITED_LIMIT = -1;

export const isUnlimitedLimit = (value: number | null | undefined): boolean =>
  value === UNLIMITED_LIMIT;

// ─── Monobank-трекер ──────────────────────────────────────────────────────

export type TrackerMode = 'ALL' | 'INCOME';

export interface MonoTracker {
  enabled: boolean;
  mode: TrackerMode;
  fields: Record<string, boolean>;
  hasToken: boolean;
  hasWebhook: boolean;
}

// ─── Звіт по картках ──────────────────────────────────────────────────────

export interface CardReportRow {
  cardId: string;
  label: string;
  bankName: string;
  lastFour: string;
  balance: number;
  status: string;
  [metric: string]: unknown;
}

// ─── Адміністрування ──────────────────────────────────────────────────────

export interface AdminUser {
  userId: number;
  telegramChatId: number;
  isActive: number;
  isAlertsActive: number;
  scannerMode: ScannerMode;
  workingCapital: number;
  minSpreadPct: number;
  createdAt: number;
}
