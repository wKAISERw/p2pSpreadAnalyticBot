export interface CompetitorOrder {
  merchantId: string;
  merchantName: string;
  price: number;
  availableAmount: number;
  minLimit: number;
  maxLimit: number;
  orderCount: number;
  finishRate: number;
}

export interface MakerOpportunity {
  id: string;
  exchange: string;
  bank: string;
  side: 'BUY' | 'SELL';
  suggestedPrice: number;
  spreadFromBest: number;
  avgSpread: number;
  marketCondition: 'favorable' | 'neutral' | 'competitive';
  competitors: CompetitorOrder[];
  marketDepth: { price: number; volume: number }[];
  totalVolume: number;
  timestamp: number;
  createAdLink: string;
}

// Mock data generator for development
export function generateMockMakerOpportunities(): MakerOpportunity[] {
  const exchanges = ['Binance', 'Bybit', 'OKX', 'MEXC'];
  const banks = ['43', '14', '64', '48'];
  const sides: ('BUY' | 'SELL')[] = ['BUY', 'SELL'];
  const conditions: ('favorable' | 'neutral' | 'competitive')[] = ['favorable', 'neutral', 'competitive'];

  return Array.from({ length: 6 }, (_, i) => {
    const basePrice = 41.2 + Math.random() * 0.5;
    const exchange = exchanges[i % exchanges.length];
    
    return {
      id: `maker-${i}`,
      exchange,
      bank: banks[i % banks.length],
      side: sides[i % 2],
      suggestedPrice: basePrice,
      spreadFromBest: (Math.random() - 0.3) * 0.5,
      avgSpread: 0.15 + Math.random() * 0.1,
      marketCondition: conditions[i % 3],
      competitors: Array.from({ length: 5 + Math.floor(Math.random() * 5) }, (_, j) => ({
        merchantId: `merchant-${i}-${j}`,
        merchantName: `Trader${100 + j}`,
        price: basePrice + (Math.random() - 0.5) * 0.3,
        availableAmount: 10000 + Math.random() * 90000,
        minLimit: 1000,
        maxLimit: 50000 + Math.random() * 50000,
        orderCount: 100 + Math.floor(Math.random() * 900),
        finishRate: 95 + Math.random() * 5,
      })),
      marketDepth: Array.from({ length: 8 }, (_, j) => ({
        price: basePrice - 0.15 + (j * 0.05),
        volume: 50000 + Math.random() * 200000,
      })),
      totalVolume: 500000 + Math.random() * 2000000,
      timestamp: Date.now() - Math.random() * 60000,
      createAdLink: `https://p2p.${exchange.toLowerCase()}.com/create-ad`,
    };
  });
}
