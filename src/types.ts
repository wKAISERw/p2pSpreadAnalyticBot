

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
}

export interface ArbitrageOpportunity {
  buyOrder: Order;
  sellOrder: Order;
  buyBank: string;
  sellBank: string;
  routeType: string;
  actualEntryUah: number;
  grossSpreadPct: number;
  netProfit: number;
  netSpread: number;
  totalFee: number;
  dealAmount: number;
  timestamp: number;
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
  goalCapital?: number; // Added for Goal Progress
  soundEnabled?: boolean; // New: Sound toggle
  soundVolume?: number; // New: Sound volume (0-1)
  accentColor?: string; // New: Theme accent
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

export interface GlobalSettings {
  riskMode: 'STRICT' | 'WARNING' | 'RELAXED';
  behaviorAlertScore: number;
  velocitySpikePerHour: number;
  stickyMinChain: number;
  reviewTtlHours: number;
  maxAlertsPerCycle: number;
}

export interface AutoTradeLog {
  id: string;
  timestamp: number;
  buyExchange: string;
  sellExchange: string;
  amountUah: number;
  expectedProfit: number;
  status: 'SUCCESS' | 'FAILED' | 'PENDING';
  errorMessage?: string;
}

export interface SystemStats {
  cycles: number;
  lastCycleMs: number;
  botsDetectedToday: number;
  spreadsFoundToday: number;
  llmQueue: number;
  reviewQueue: number;
  cbStatus: Record<string, string>;
  isScannerActive: boolean;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
  details?: any;
  buyExchange?: string;
  sellExchange?: string;
  amountUah?: number;
  expectedProfit?: number;
  status?: string;
}