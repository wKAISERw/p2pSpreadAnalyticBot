/**
 * Демо-дані maker-режиму.
 *
 * Розрахунки повторюють формули з core/engine/price_advisor.py, щоб UI
 * показував ті самі величини й ті самі співвідношення, що бот надсилає
 * в Telegram. Дані ринку — синтетичні: ендпоінта під maker ще немає.
 *
 * Генератор детермінований (без Math.random у рендері), інакше цифри
 * стрибали б на кожен перерендер і на них не можна було б дивитись.
 */
import {
  BuyAdvice, CompetitorOrder, IncomingOrder, MakerAd, MakerSpeed, SellAdvice,
} from '../types/maker';

/** Простий LCG — стабільний «шум» від seed. */
function seeded(seed: number) {
  let state = seed % 2147483647;
  if (state <= 0) state += 2147483646;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

export function demoCompetitors(exchange: string, base = 41.3, count = 9): CompetitorOrder[] {
  const rand = seeded(exchange.length * 977 + 13);

  return Array.from({ length: count }, (_, i) => {
    const r = rand();
    return {
      merchantId: `${exchange.toLowerCase()}-m${i}`,
      merchantName: ['UAHPro', 'FastChange', 'CryptoDim', 'P2P_Master', 'ObmenUA',
        'SwiftPay', 'MonoTrade', 'NightOwl', 'TopRate'][i % 9],
      price: Number((base + i * 0.035 + r * 0.02).toFixed(4)),
      availableAmount: Math.round(400 + r * 2600),
      minLimit: 500,
      maxLimit: Math.round(15000 + r * 60000),
      orderCount: Math.round(120 + r * 3500),
      finishRate: Number((95 + r * 5).toFixed(1)),
      isVerified: r > 0.45,
    };
  }).sort((a, b) => a.price - b.price);
}

/** PriceAdvisor.suggest_buy_price() */
export function computeBuyAdvice(
  sellBookTop: number,
  amountUsdt: number,
  targetMargin: number,
  networkFee: number,
): BuyAdvice {
  const feePerUsdt = networkFee / Math.max(amountUsdt, 1);
  const maxBuyPrice = (sellBookTop - feePerUsdt) / (1 + targetMargin);
  const estimatedProfit = (amountUsdt - networkFee) * sellBookTop - amountUsdt * maxBuyPrice;

  return {
    maxBuyPrice: Number(maxBuyPrice.toFixed(4)),
    sellBookTop,
    networkFee,
    targetMarginPct: targetMargin * 100,
    estimatedProfitUah: Number(estimatedProfit.toFixed(2)),
  };
}

/** PriceAdvisor.suggest_sell_price() + analyze_sell_depth() */
export function computeSellAdvice(
  buyPrice: number,
  amountUsdt: number,
  minMargin: number,
  networkFee: number,
  competitors: CompetitorOrder[],
  speed: MakerSpeed,
): SellAdvice {
  const feePerUsdt = networkFee / Math.max(amountUsdt, 1);
  const absoluteMinSell = buyPrice * (1 + minMargin) + feePerUsdt;
  const sellAmountAfterFee = amountUsdt - networkFee;
  const profitAtMin = sellAmountAfterFee * absoluteMinSell - amountUsdt * buyPrice;

  const sorted = [...competitors].sort((a, b) => a.price - b.price);
  const topPrice = sorted[0]?.price ?? absoluteMinSell * 1.015;

  // Шукаємо ціну, перед якою вже накопичено 1.5× нашого об'єму:
  // ставати далі немає сенсу — черга попереду не розсмокчеться.
  let accumulated = 0;
  let wallPrice = topPrice;
  for (const o of sorted) {
    wallPrice = o.price;
    accumulated += wallPrice > 0 ? o.maxLimit / wallPrice : 0;
    if (accumulated >= amountUsdt * 1.5) break;
  }

  let recommended = speed === 'ANY' ? topPrice - 0.0001 : wallPrice - 0.0001;
  let reason = speed === 'ANY'
    ? 'Перебито топ-1 ціну (швидкість неважлива)'
    : `Оптимальна ціна перед стінкою ліквідності (${Math.round(accumulated)} USDT)`;

  // Safety breaker: нижче мінімальної маржі не опускаємось.
  if (recommended < absoluteMinSell) {
    recommended = absoluteMinSell;
    reason += ' · спрацював Market Stop-Loss, ціну підтягнуто до мін. маржі';
  }

  const estimatedProfit = sellAmountAfterFee * recommended - amountUsdt * buyPrice;

  return {
    minSellPrice: Number(absoluteMinSell.toFixed(4)),
    buyPrice,
    networkFee,
    amountUsdt,
    sellAmountAfterFee: Number(sellAmountAfterFee.toFixed(4)),
    minMarginPct: minMargin * 100,
    profitAtMinUah: Number(profitAtMin.toFixed(2)),
    profitAtMinPct: Number(((profitAtMin / (amountUsdt * buyPrice)) * 100).toFixed(2)),
    recommendedPrice: Number(recommended.toFixed(4)),
    absoluteMinSell: Number(absoluteMinSell.toFixed(4)),
    competitorPrice: topPrice,
    reason,
    estimatedProfit: Number(estimatedProfit.toFixed(2)),
    wallPrice: Number(wallPrice.toFixed(4)),
    wallVolumeUsdt: Math.round(accumulated),
  };
}

export function demoIncomingOrders(exchange: string): IncomingOrder[] {
  const now = Date.now();
  return [
    {
      orderId: '1794523310299283456',
      itemId: 'AD-88213',
      exchange,
      price: 41.42,
      amountUsdt: 250,
      totalFiat: 10355,
      createdAt: now - 45_000,
      counterparty: {
        merchantId: 'm-9931',
        merchantName: 'FastChange',
        finishRate: 99.2,
        monthOrderCount: 1840,
        isVerified: true,
      },
      rec: 'OK',
      reason: 'Мерчант перевірений, скарг немає, умови без вимоги чеків.',
    },
    {
      orderId: '1794523310299283999',
      itemId: 'AD-88213',
      exchange,
      price: 41.42,
      amountUsdt: 620,
      totalFiat: 25680,
      createdAt: now - 20_000,
      counterparty: {
        merchantId: 'm-4410',
        merchantName: 'NightOwl',
        finishRate: 96.4,
        monthOrderCount: 210,
        isVerified: false,
        riskFlag: 'CHAT_FIRST:RECEIPT_REQUIRED:S35',
      },
      rec: 'WARN',
      reason: 'Просить писати в чат до оплати і вимагає чек — типова схема тиску на апеляцію.',
    },
    {
      orderId: '1794523310299284111',
      itemId: 'AD-88213',
      exchange,
      price: 41.42,
      amountUsdt: 1200,
      totalFiat: 49704,
      createdAt: now - 8_000,
      counterparty: {
        merchantId: 'm-1287',
        merchantName: 'ObmenUA',
        finishRate: 88.1,
        monthOrderCount: 64,
        isVerified: false,
        riskFlag: 'BLOCK:TRIANGLE:S82',
      },
      rec: 'BLOCK',
      reason: 'Оплата з реквізитів третьої особи — ознака трикутника. Угоду краще відхилити.',
    },
  ];
}

export function demoAd(exchange: string, side: 'MAKER_BUY' | 'MAKER_SELL', price: number): MakerAd {
  return {
    itemId: 'AD-88213',
    exchange,
    side,
    bank: '43',
    price,
    remainingUsdt: 1830,
    totalUsdt: 3000,
    isOnline: true,
    bookPosition: 3,
    competitorsAhead: 2,
  };
}
