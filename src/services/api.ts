/// <reference types="vite/client" />
import axios, { AxiosError } from 'axios';
import {
  SystemStats,
  ArbitrageOpportunity,
  GlobalSettings,
  GlobalSettingsRaw,
  GlobalSettingKey,
  GLOBAL_SETTING_KINDS,
  UserSettings,
  BlacklistEntry,
  BlacklistScope,
  ApiKeyConfig,
  ExchangeStatus,
  ExchangeHealthResult,
  TakerSide,
  TakerOrdersResponse,
  PriceRange,
  BankScopes,
  BankSide,
  ScannerMode,
  LogRecord,
  DetailedStats,
  TelegramSyncData,
  UserFilters,
  UserFiltersPatch,
  MerchantThresholds,
  Bank,
  ScannerState,
  Card,
  CardCreatePayload,
  CardPatch,
  CardTransaction,
  SessionStatus,
  MonitoringOrder,
  QueueStatus,
  AuthSession,
  AuthConfig,
  TelegramWidgetPayload,
  DisplaySettings,
  DisplaySettingsPatch,
  AutoCooldownTier,
  MerchantThresholdsFull,
  SniperRule,
  FeatureGroup,
  UsedSubsidy,
  CardDisplaySettings,
  BankLimits,
  MonoTracker,
  TrackerMode,
  CardReportRow,
  AdminUser,
} from '../types';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';

/**
 * Чи не заблокує браузер запити ще до відправки.
 *
 * Класична пастка: сторінка віддається по https, а VITE_API_URL лишився
 * абсолютною http-адресою. Браузер ріже це як mixed content — запит не
 * йде взагалі, axios не бачить відповіді, і все виглядає як «бекенд
 * недоступний», хоча бекенд живий і чудово відповідає.
 *
 * Повертає опис проблеми або null, якщо все гаразд.
 */
export function diagnoseApiBase(): string | null {
  if (typeof window === 'undefined') return null;

  const pageIsSecure = window.location.protocol === 'https:';
  const apiIsPlain = /^http:\/\//i.test(API_BASE_URL);

  if (pageIsSecure && apiIsPlain) {
    return (
      `Сторінка відкрита по HTTPS, а API вказаний як ${API_BASE_URL} — ` +
      'браузер блокує такі запити як mixed content ще до відправки. ' +
      'Бекенд при цьому може бути цілком живий. ' +
      'Треба зібрати фронтенд із відносним шляхом (VITE_API_URL=/api/v1).'
    );
  }

  return null;
}

// ─── Ключ доступу ─────────────────────────────────────────────────────────
//
// Бекенд захищає весь /api/v1/* заголовком X-API-Key (api/security.py).
// Ключ береться з localStorage, щоб не запікати спільний секрет у бандл;
// VITE_API_KEY лишається як зручність для локальної розробки.
// При деплої за nginx-проксі (див. deploy/nginx.conf) ключ підставляє
// проксі, а браузеру він не потрібен узагалі.

const API_KEY_STORAGE = 'arbix-api-key';
const SESSION_STORAGE = 'arbix-session';

export function getApiKey(): string {
  try {
    return localStorage.getItem(API_KEY_STORAGE) || import.meta.env.VITE_API_KEY || '';
  } catch {
    return import.meta.env.VITE_API_KEY || '';
  }
}

export function setApiKey(key: string): void {
  try {
    if (key) localStorage.setItem(API_KEY_STORAGE, key);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    /* приватний режим браузера — переживемо */
  }
}

// ─── Сесія користувача ────────────────────────────────────────────────────
//
// X-API-Key каже «цьому клієнту можна стукати в API», але нічого не каже про
// те, ХТО стукає. Особу підтверджує окремий session-токен, виданий після
// входу через Telegram: саме з нього бекенд бере telegram_id, замість того
// щоб вірити параметру запиту.

export function getSessionToken(): string {
  try {
    return localStorage.getItem(SESSION_STORAGE) || '';
  } catch {
    return '';
  }
}

export function setSessionToken(token: string): void {
  try {
    if (token) localStorage.setItem(SESSION_STORAGE, token);
    else localStorage.removeItem(SESSION_STORAGE);
  } catch {
    /* приватний режим браузера */
  }
}

// ─── Помилки ──────────────────────────────────────────────────────────────
//
// Раніше кожен метод ковтав помилку і повертав false/[]. Через це "немає
// зв'язку", "невірний ключ" і "порожні дані" виглядали для UI однаково.

export type ApiErrorKind = 'auth' | 'offline' | 'server' | 'client';

export class ApiError extends Error {
  kind: ApiErrorKind;
  status?: number;

  constructor(kind: ApiErrorKind, message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
  }
}

function toApiError(error: AxiosError): ApiError {
  const status = error.response?.status;
  const detail = (error.response?.data as any)?.detail;

  if (status === 401 || status === 403) {
    return new ApiError('auth', detail || 'Невірний або відсутній API-ключ', status);
  }
  if (!error.response) {
    return new ApiError('offline', 'Бекенд недоступний', undefined);
  }
  if (status && status >= 500) {
    return new ApiError('server', detail || `Помилка сервера (${status})`, status);
  }
  return new ApiError('client', detail || error.message, status);
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
});

apiClient.interceptors.request.use((config) => {
  const key = getApiKey();
  if (key) config.headers.set('X-API-Key', key);

  const session = getSessionToken();
  if (session) config.headers.set('Authorization', `Bearer ${session}`);

  return config;
});

apiClient.interceptors.response.use(
  (response) => response.data,
  (error: AxiosError) => {
    const apiError = toApiError(error);
    if (apiError.kind !== 'offline') {
      console.error(`[API ${apiError.kind}] ${error.config?.url}: ${apiError.message}`);
    }
    return Promise.reject(apiError);
  }
);

// ─── Глобальні налаштування: рядки на дроті ↔ типи в UI ───────────────────

function parseGlobalSettings(raw: GlobalSettingsRaw): GlobalSettings {
  const parsed: Record<string, unknown> = {};

  for (const [key, kind] of Object.entries(GLOBAL_SETTING_KINDS)) {
    const value = raw[key];
    if (value === null || value === undefined || value === '') continue;

    switch (kind) {
      case 'int': {
        const n = parseInt(String(value), 10);
        if (!Number.isNaN(n)) parsed[key] = n;
        break;
      }
      case 'float': {
        const n = parseFloat(String(value));
        if (!Number.isNaN(n)) parsed[key] = n;
        break;
      }
      case 'bool':
        parsed[key] = String(value).toLowerCase() === 'true';
        break;
      default:
        parsed[key] = String(value);
    }
  }

  return parsed as GlobalSettings;
}

function serializeGlobalSettings(patch: Partial<GlobalSettings>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined || value === null) continue;
    out[key] = typeof value === 'boolean' ? String(value) : String(value);
  }
  return out;
}

export interface GlobalSettingsUpdateResult {
  status: string;
  applied: Record<string, string>;
  rejected: string[];
}

/** Чиї угоди рахує аналітика. 'all' бекенд дозволяє лише адміну. */
export type StatsScope = 'mine' | 'all';

// ─── Публічний клієнт ─────────────────────────────────────────────────────

export const api = {
  getStats: () => apiClient.get<any, SystemStats>('/stats'),

  /** Доступність бірж: enabled / cooldown / лічильник відмов. */
  getExchanges: () => apiClient.get<any, ExchangeStatus[]>('/exchanges'),

  /**
   * Повна аналітика: summary, proposals, daily, exchanges, banks, heatmap, weekly.
   *
   * scope='mine' рахує лише власні угоди — це те саме, що «💼 Моя статистика»
   * в боті. 'all' бекенд віддає тільки адміну: раніше параметра не було
   * взагалі і кожен бачив зведений PnL усіх користувачів.
   */
  getDetailedStats: (periodDays = 30, scope: StatsScope = 'mine') =>
    apiClient.get<any, DetailedStats>('/stats/detailed', {
      params: { period: periodDays, scope },
    }),

  getOpportunities: () => apiClient.get<any, ArbitrageOpportunity[]>('/opportunities'),

  /** Останні записи логу сканера, найновіші першими. Бекенд ріже limit до 500. */
  getLogs: (limit = 200) => apiClient.get<any, LogRecord[]>('/logs', { params: { limit } }),

  /** Свій список плюс спільний — кожен запис підписаний полем scope. */
  getBlacklist: () => apiClient.get<any, BlacklistEntry[]>('/blacklist'),

  /** scope='global' бекенд приймає лише від адміністратора. */
  addToBlacklist: (
    entry: Omit<BlacklistEntry, 'addedAt' | 'scope'>,
    scope: BlacklistScope = 'personal'
  ) => apiClient.post('/blacklist', entry, { params: { scope } }),

  removeFromBlacklist: (
    merchantId: string,
    exchange: string,
    scope: BlacklistScope = 'personal'
  ) => apiClient.delete(`/blacklist/${exchange}/${merchantId}`, { params: { scope } }),

  async getGlobalSettings(): Promise<GlobalSettings> {
    const raw = await apiClient.get<any, GlobalSettingsRaw>('/settings/global');
    return parseGlobalSettings(raw);
  },

  /**
   * Пише лише передані ключі. Бекенд відповідає списками applied/rejected —
   * не ковтаємо їх, бо саме там видно, що ключ не дійшов.
   */
  updateGlobalSettings(patch: Partial<GlobalSettings>): Promise<GlobalSettingsUpdateResult> {
    return apiClient.post<any, GlobalSettingsUpdateResult>(
      '/settings/global',
      serializeGlobalSettings(patch)
    );
  },

  /** Зручна обгортка: старт/стоп ядра сканера через runtime_config. */
  setScannerActive(active: boolean): Promise<GlobalSettingsUpdateResult> {
    return this.updateGlobalSettings({ isScannerActive: active } as Partial<GlobalSettings>);
  },

  getUserSettings: (telegramId: string | number) =>
    apiClient.get<any, Record<string, unknown>>('/settings/user', {
      params: { telegram_id: telegramId },
    }),

  getAccounts: (telegramId: string | number) =>
    apiClient.get<any, any[]>(`/accounts/${telegramId}`),

  /**
   * Кладе ключі біржі в зашифроване сховище бота.
   * telegram_id обов'язковий: без нього бекенд запише креденшли під user_id=0,
   * і жоден реальний користувач їх не побачить.
   */
  saveCredentials: (exchange: string, keys: ApiKeyConfig, telegramId: string | number) =>
    apiClient.post(`/credentials/${exchange.toLowerCase()}`, keys, {
      params: { telegram_id: telegramId },
    }),

  /** Відв'язує біржу в боті, а не лише в браузері. */
  deleteCredentials: (exchange: string, telegramId: string | number) =>
    apiClient.delete(`/credentials/${exchange.toLowerCase()}`, {
      params: { telegram_id: telegramId },
    }),

  syncTelegram: (telegramId: string | number) =>
    apiClient.get<any, TelegramSyncData>(`/telegram/sync/${telegramId}`),

  /**
   * Персональні налаштування. telegramUserId обов'язковий — бекенд без нього
   * віддає 400, і раніше ця помилка тихо губилась.
   */
  updateTelegramSettings: (
    telegramId: string | number,
    settings: Partial<UserSettings> & Record<string, unknown>
  ) => apiClient.post('/user/settings', { ...settings, telegramUserId: telegramId }),

  // ─── Керування (api/routers/control.py) ─────────────────────────────────

  getUserFilters: (telegramId: string | number) =>
    apiClient.get<any, UserFilters>('/user/filters', { params: { telegram_id: telegramId } }),

  /**
   * Групи фільтрів окремими запитами.
   *
   * Потрібні, щоб синхронізація могла перечитувати пресети тейкера, не
   * чіпаючи спредові пороги (і навпаки): поки все приходило однією
   * відповіддю, роздільні перемикачі були б декорацією.
   */
  getCoreFilters: () => apiClient.get<any, UserFilters>('/user/filters/core'),
  getTakerFilters: () => apiClient.get<any, UserFilters>('/user/filters/taker'),

  /** Банки в розрізі режимів: спільні, винятки і те, що вийде насправді. */
  getBankScopes: () => apiClient.get<any, BankScopes>('/user/bank-scopes'),

  /** Порожній `banks` знімає виняток — режим повертається до спільних. */
  setBankScope: (mode: ScannerMode, side: BankSide, banks: string[]) =>
    apiClient.post<any, { status: string; banks: string[] }>('/user/bank-scopes', {
      mode, side, banks,
    }),

  /** Ціновий фільтр входу — лише для спред-режиму. */
  getPriceRange: () => apiClient.get<any, PriceRange>('/user/price-range'),
  setPriceRange: (config: PriceRange) =>
    apiClient.post<any, { status: string; priceRange: PriceRange }>('/user/price-range', {
      mode: config.mode ?? '',
      min: config.min ?? 0,
      max: config.max ?? 0,
      value: config.value ?? 0,
    }),

  /** Пороги мерчанта: загальні та перевизначені по біржах. */
  getMerchantFilters: () =>
    apiClient.get<any, {
      merchantFilters: MerchantThresholdsFull;
      exchangeMerchantFilters: Record<string, MerchantThresholdsFull>;
    }>('/user/merchant-filters'),

  /**
   * Ордери, що проходять тейкер-фільтри. `side` за замовчуванням 'both' —
   * дивитись обидві сторони можна незалежно від того, який scanner_mode
   * зараз стоїть у бота.
   */
  getTakerOrders: (side: TakerSide = 'both', limit = 50) =>
    apiClient.get<any, TakerOrdersResponse>('/taker/orders', { params: { side, limit } }),

  /** Пише лише передані поля — решта колонок scanner_users не чіпається. */
  updateUserFilters: (telegramId: string | number, patch: UserFiltersPatch) =>
    apiClient.post<any, { status: string; updated: string[]; rejected: string[] }>(
      '/user/filters',
      { telegramId: Number(telegramId), ...patch }
    ),

  /**
   * Пороги мерчанта. Без exchange пишуться загальні, з exchange —
   * правило для конкретної біржі, яке перебиває загальне.
   */
  updateMerchantFilters: (thresholds: MerchantThresholdsFull & { exchange?: string }) =>
    apiClient.post<any, { status: string; scope: string; updated: string[] }>(
      '/user/merchant-filters',
      thresholds
    ),

  getBanks: () => apiClient.get<any, Bank[]>('/banks'),

  getScannerState: () => apiClient.get<any, ScannerState>('/scanner/state'),
  startScanner: () => apiClient.post('/scanner/start'),
  stopScanner: () => apiClient.post('/scanner/stop'),
  /** hours = 0 знімає паузу. */
  setMute: (hours: number) => apiClient.post('/scanner/mute', { hours }),

  enableExchange: (name: string) => apiClient.post(`/exchanges/${name}/enable`),
  disableExchange: (name: string, cooldownHours = 0, reason = 'manual (web)') =>
    apiClient.post(`/exchanges/${name}/disable`, { reason, cooldownHours }),

  /**
   * Опитує всі біржі по три спроби кожну — звідси довгий таймаут.
   * Викликається лише руками: це не метрика, а діагностика.
   */
  checkExchangesHealth: () =>
    apiClient.post<any, ExchangeHealthResult[]>('/exchanges/health', undefined, {
      timeout: 90000,
    }),

  getCards: (telegramId: string | number) =>
    apiClient.get<any, Card[]>('/cards', { params: { telegram_id: telegramId } }),
  getCardTransactions: (cardId: string, limit = 50) =>
    apiClient.get<any, CardTransaction[]>(`/cards/${cardId}/transactions`, { params: { limit } }),

  /**
   * Заведення картки. Потрібен або повний номер (16 цифр — за ним бот
   * звіряє виписку Monobank), або останні чотири, якщо звірка не потрібна.
   */
  createCard: (payload: CardCreatePayload) =>
    apiClient.post<any, { status: string; cardId: string }>('/cards', payload),

  /** Пише лише передані поля — решта колонок картки не чіпається. */
  updateCard: (cardId: string, patch: CardPatch) =>
    apiClient.post<any, { status: string; updated: string[] }>(`/cards/${cardId}`, patch),

  deleteCard: (cardId: string) => apiClient.delete(`/cards/${cardId}`),

  /** Свіжість сесій бірж поточного користувача — id бере бекенд із токена. */
  getSessions: () => apiClient.get<any, SessionStatus[]>('/monitoring/sessions'),
  getMonitoringOrders: () => apiClient.get<any, MonitoringOrder[]>('/monitoring/orders'),
  getQueues: () => apiClient.get<any, QueueStatus>('/monitoring/queues'),

  // ─── Налаштування виводу ────────────────────────────────────────────────

  getDisplaySettings: () => apiClient.get<any, DisplaySettings>('/user/display'),

  updateDisplaySettings: (patch: DisplaySettingsPatch) =>
    apiClient.post<any, { status: string; updated: string[] }>('/user/display', patch),

  updateAutoCooldown: (windowSeconds: number, tiers: AutoCooldownTier[]) =>
    apiClient.post('/user/display/auto-cooldown', { windowSeconds, tiers }),

  // ─── Снайпер-правила ────────────────────────────────────────────────────

  getSniperRules: () => apiClient.get<any, SniperRule[]>('/user/sniper'),
  /** Замінює весь набір — так само, як меню /sniper у боті. */
  updateSniperRules: (rules: SniperRule[]) =>
    apiClient.post<any, { status: string; count: number }>('/user/sniper', { rules }),

  // ─── Експериментальні фічі ──────────────────────────────────────────────

  getFeatures: () => apiClient.get<any, FeatureGroup[]>('/user/features'),
  toggleFeature: (key: string) =>
    apiClient.post<any, { status: string; key: string; enabled: boolean }>(
      '/user/features/toggle',
      { key }
    ),

  getSubsidies: () => apiClient.get<any, UsedSubsidy[]>('/user/subsidies'),

  // ─── Картковий модуль виводу ────────────────────────────────────────────

  getCardDisplay: () => apiClient.get<any, CardDisplaySettings>('/user/card-display'),
  updateCardDisplay: (patch: Partial<CardDisplaySettings>) =>
    apiClient.post<any, { status: string; updated: string[] }>('/user/card-display', patch),

  // ─── Ліміти ─────────────────────────────────────────────────────────────

  getBankLimits: () => apiClient.get<any, BankLimits[]>('/user/bank-limits'),
  /** limits — snake_case ключі з LIMIT_FIELDS. */
  setBankLimits: (bankName: string, limits: Record<string, number>) =>
    apiClient.post('/user/bank-limits', { bankName, limits }),

  setCardLimits: (cardId: string, limits: Record<string, number>) =>
    apiClient.post(`/cards/${cardId}/limits`, { limits }),
  toggleCardCustomLimits: (cardId: string, enabled: boolean) =>
    apiClient.post(`/cards/${cardId}/custom-limits`, { enabled }),

  // ─── Monobank-трекер ────────────────────────────────────────────────────

  getMonoTracker: (cardId: string) =>
    apiClient.get<any, MonoTracker>(`/cards/${cardId}/mono-tracker`),
  updateMonoTracker: (
    cardId: string,
    patch: { enabled?: boolean; mode?: TrackerMode; fields?: Record<string, boolean> }
  ) => apiClient.post(`/cards/${cardId}/mono-tracker`, patch),

  // ─── Звіти ──────────────────────────────────────────────────────────────

  getCardsReport: () => apiClient.get<any, CardReportRow[]>('/cards/report'),

  // ─── Адміністрування ────────────────────────────────────────────────────

  getUsers: () => apiClient.get<any, AdminUser[]>('/admin/users'),
  updateUserState: (userId: number, patch: { isActive?: boolean; isAlertsActive?: boolean }) =>
    apiClient.post(`/admin/users/${userId}`, patch),
};

// ─── Автентифікація ───────────────────────────────────────────────────────

export const authApi = {
  getConfig: () => apiClient.get<any, AuthConfig>('/auth/config'),

  /** Вхід одним кліком. Потребує домену, прописаного в BotFather. */
  loginWithWidget: (payload: TelegramWidgetPayload) =>
    apiClient.post<any, AuthSession>('/auth/telegram/widget', payload),

  /** Вхід кодом із команди /login у боті — працює і на localhost. */
  loginWithCode: (code: string) =>
    apiClient.post<any, AuthSession>('/auth/telegram/code', { code }),

  /** Вхід уже прив'язаним Google. Сам по собі акаунт не створює. */
  loginWithGoogle: (googleUid: string) =>
    apiClient.post<any, AuthSession>('/auth/google', { googleUid }),

  me: () => apiClient.get<any, Omit<AuthSession, 'token'> & { isRegistered: boolean }>('/auth/me'),

  linkGoogle: (googleUid: string, email = '') =>
    apiClient.post<any, { status: string; identities: AuthSession['identities'] }>(
      '/auth/link/google',
      { googleUid, email }
    ),

  unlinkGoogle: () => apiClient.post('/auth/unlink/google'),
};

// ─── Стан підключення ─────────────────────────────────────────────────────

export type ConnectionState = 'connecting' | 'live' | 'unauthorized' | 'offline' | 'error';

export async function checkConnection(): Promise<ConnectionState> {
  try {
    await api.getStats();
    return 'live';
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.kind === 'auth') return 'unauthorized';
      if (error.kind === 'offline') return 'offline';
      return 'error';
    }
    return 'offline';
  }
}
