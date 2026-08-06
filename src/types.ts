// Типи фронтенду. Дзеркалять те, що реально віддає FastAPI-бекенд бота
// (api/routers/dashboard.py). Бекенд проганяє всі відповіді через
// dict_to_camel, тому тут скрізь camelCase.

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
  riskScore?: number;
  riskFlag?: string;
  isVerified?: boolean;
  // Сканер віддає це поле з березня, у типі його не було.
  lastOnlineMins?: number | null;
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
}

export interface ApiKeyConfig {
  key: string;
  secret: string;
  passphrase?: string;
}

export interface AutoTradeConfig {
  enabled: boolean;
  maxTradeAmount: number;
  minSpread: number;
  allowedExchanges: string[];
  maxRiskScore: number;
  maxCapital?: number;
}

export interface MerchantFilters {
  minOrders: number;
  minRate: number;
}

export interface SyncPreferences {
  capital: boolean;
  spread: boolean;
  banks: boolean;
  apiKeys: boolean;
}

export interface UserSettings {
  minCapital: number;
  maxCapital: number;
  minSpread: number;
  banks: string[];
  merchantFilters?: MerchantFilters;
  autoTrade?: AutoTradeConfig;
  apiKeys?: Record<string, ApiKeyConfig>;
  telegramUserId?: string;
  isTelegramAdmin?: boolean;
  autoSyncTelegram?: boolean;
  goalCapital?: number;
  soundEnabled?: boolean;
  soundVolume?: number;
  /** Відтінок акценту в OKLCH (0–360). Рядки лишились із часів,
   *  коли зберігалась назва кольору — resolveHue() їх розуміє. */
  accentColor?: number | string;
  syncPreferences?: SyncPreferences;
}

export interface BlacklistEntry {
  exchange: string;
  merchantId: string;
  merchantName: string;
  reason: string;
  source: string;
  addedAt: number;
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
    minCapital: number;
    maxCapital: number;
    minSpread: number;
    banks: string[];
  };
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
  scannerMode: ScannerMode;
  makerBuyPrice: number;
  targetMargin: number;
  isAlertsActive?: number;
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
  isAlertsActive?: boolean;
  targetMargin?: number;
  makerBuyPrice?: number;
}

export interface Bank {
  code: string;
  name: string;
}

export interface ScannerState {
  isScannerActive: boolean;
  isMuted: boolean;
  muteSecondsLeft: number;
}

export interface CardLimits {
  [key: string]: number | string | boolean | null;
}

export interface Card {
  id: string;
  ownerId: number;
  bankName: string;
  lastFour: string;
  label: string;
  balance: number;
  status?: string;
  limits: CardLimits;
  usedDaily: { in: number; out: number };
  usedMonthly: { in: number; out: number };
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

export interface CardDisplaySettings {
  cardModuleMode: CardModuleMode;
  cardOutputMode: CardOutputMode;
  enableSmartSpoiler: boolean;
  cardDetailLevel: CardDetailLevel;
  enableInSingleModes: boolean;
  showBalancesBreakdown: boolean;
  showTransferTips: boolean;
  coldCardLimit: number;
}

// ─── Ліміти ───────────────────────────────────────────────────────────────

/** Поля, які приймають set_user_bank_limit і update_card_limit_override. */
export const LIMIT_FIELDS = [
  'daily_out_max', 'daily_in_max', 'monthly_out_max', 'monthly_in_max',
  'max_single_tx_out', 'max_single_tx_in', 'max_tx_per_day', 'cooldown_hours',
] as const;

export type LimitField = (typeof LIMIT_FIELDS)[number];

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
}

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
