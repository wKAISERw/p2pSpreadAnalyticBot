import { ArbitrageOpportunity, SystemStats, AutoTradeLog, GlobalSettings, UserSettings, BlacklistEntry } from '../types';

export const mockStats: SystemStats = {
  cycles: 1423,
  lastCycleMs: 1250,
  botsDetectedToday: 42,
  spreadsFoundToday: 18,
  llmQueue: 2,
  reviewQueue: 5,
  cbStatus: {
    Bybit: 'CLOSED',
    Binance: 'CLOSED',
    OKX: 'CLOSED',
    MEXC: 'CLOSED',
    Wallet: 'CLOSED'
  },
  isScannerActive: true
};

export const mockOpportunities: ArbitrageOpportunity[] = [
  {
    buyOrder: {
      id: 'b1',
      price: 41.50,
      availableAmount: 500,
      minLimit: 1000,
      maxLimit: 20000,
      merchantId: 'm1',
      merchantName: 'CryptoKing',
      orderCount: 1250,
      finishRate: 99.5,
      exchange: 'Bybit',
      link: '#',
      bankCodes: ['43'],
      riskScore: 10,
      riskFlag: 'OK',
      isVerified: true
    },
    sellOrder: {
      id: 's1',
      price: 42.10,
      availableAmount: 300,
      minLimit: 5000,
      maxLimit: 15000,
      merchantId: 'm2',
      merchantName: 'FastTradeUA',
      orderCount: 450,
      finishRate: 98.2,
      exchange: 'OKX',
      link: '#',
      bankCodes: ['43'],
      riskScore: 25,
      riskFlag: 'REGEX_WEAK:RECEIPT_REQUIRED:S10',
      isVerified: false
    },
    buyBank: 'Monobank',
    sellBank: 'Monobank',
    routeType: 'CROSS',
    actualEntryUah: 12450,
    grossSpreadPct: 1.44,
    netProfit: 135,
    netSpread: 1.08,
    totalFee: 42.10,
    dealAmount: 12450,
    timestamp: Date.now() - 5000
  },
  {
    buyOrder: {
      id: 'b2',
      price: 41.65,
      availableAmount: 1000,
      minLimit: 500,
      maxLimit: 50000,
      merchantId: 'm3',
      merchantName: 'P2P_Master',
      orderCount: 3200,
      finishRate: 99.9,
      exchange: 'Binance',
      link: '#',
      bankCodes: ['14'],
      riskScore: 5,
      riskFlag: 'OK',
      isVerified: true
    },
    sellOrder: {
      id: 's2',
      price: 41.95,
      availableAmount: 800,
      minLimit: 1000,
      maxLimit: 30000,
      merchantId: 'm4',
      merchantName: 'UAH_Exchange',
      orderCount: 890,
      finishRate: 97.5,
      exchange: 'Bybit',
      link: '#',
      bankCodes: ['14'],
      riskScore: 45,
      riskFlag: 'LLM_PENDING:CHAT_FIRST:S30',
      isVerified: false
    },
    buyBank: 'PrivatBank',
    sellBank: 'PrivatBank',
    routeType: 'CROSS',
    actualEntryUah: 25000,
    grossSpreadPct: 0.72,
    netProfit: 138,
    netSpread: 0.55,
    totalFee: 41.95,
    dealAmount: 25000,
    timestamp: Date.now() - 15000
  }
];

export const mockLogs: AutoTradeLog[] = [
  {
    id: 'log1',
    timestamp: Date.now() - 3600000,
    buyExchange: 'Bybit',
    sellExchange: 'OKX',
    amountUah: 15000,
    expectedProfit: 180,
    status: 'SUCCESS'
  },
  {
    id: 'log2',
    timestamp: Date.now() - 7200000,
    buyExchange: 'Binance',
    sellExchange: 'Bybit',
    amountUah: 8500,
    expectedProfit: 75,
    status: 'FAILED',
    errorMessage: 'API Rate Limit Exceeded'
  }
];

export const mockGlobalSettings: GlobalSettings = {
  riskMode: 'WARNING',
  behaviorAlertScore: 60,
  velocitySpikePerHour: 20.0,
  stickyMinChain: 3,
  reviewTtlHours: 24.0,
  maxAlertsPerCycle: 5
};

export const mockUserSettings: UserSettings = {
  minCapital: 5000,
  maxCapital: 15000,
  minSpread: 0.5,
  banks: ['43', '14'],
  merchantFilters: {
    minOrders: 50,
    minRate: 95.0
  },
  autoTrade: {
    enabled: false,
    maxTradeAmount: 5000,
    minSpread: 0.8,
    allowedExchanges: ['Bybit', 'OKX'],
    maxRiskScore: 30,
    maxCapital: 15000
  },
  apiKeys: {}
};

export const mockBlacklist: BlacklistEntry[] = [
  {
    exchange: 'Binance',
    merchantId: 's42ee507f2dc',
    merchantName: 'ScamTrader99',
    reason: '🚫 ТРЕТІ ОСОБИ (Ручний Blacklist)',
    source: 'manual_tg',
    addedAt: Date.now() - 86400000 * 2
  },
  {
    exchange: 'Bybit',
    merchantId: '123456789',
    merchantName: 'FastMoney',
    reason: 'BLOCK:CASINO:Казино / беттинг',
    source: 'regex',
    addedAt: Date.now() - 86400000 * 5
  },
  {
    exchange: 'OKX',
    merchantId: '987654321',
    merchantName: 'CryptoWhale',
    reason: 'BLOCK:BADREVIEWS:25% neg (5/20) | Зробив рефанд через банк',
    source: 'llm',
    addedAt: Date.now() - 3600000 * 12
  }
];
