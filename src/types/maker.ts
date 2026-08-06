/**
 * Моделі maker-режиму. Дзеркалять те, чим оперує бот:
 *   core/engine/price_advisor.py  — розрахунок рекомендованої ціни
 *   core/engine/maker_ad_monitor.py — вхідні ордери на власне оголошення
 *
 * Реального HTTP-ендпоінта під це ще немає: сторінка працює на демо-даних
 * (src/data/makerDemo.ts), щоб можна було оцінити UX до підключення.
 */

export type MakerSide = 'MAKER_BUY' | 'MAKER_SELL';

/** Швидкість продажу: FAST враховує стінки ліквідності, ANY просто перебиває топ-1. */
export type MakerSpeed = 'FAST' | 'ANY';

/** Конкурент у стакані. */
export interface CompetitorOrder {
  merchantId: string;
  merchantName: string;
  price: number;
  availableAmount: number;
  minLimit: number;
  maxLimit: number;
  orderCount: number;
  finishRate: number;
  isVerified?: boolean;
}

/** Вихід PriceAdvisor.suggest_buy_price(). */
export interface BuyAdvice {
  maxBuyPrice: number;
  sellBookTop: number;
  networkFee: number;
  targetMarginPct: number;
  estimatedProfitUah: number;
}

/** Вихід PriceAdvisor.suggest_sell_price() + analyze_sell_depth(). */
export interface SellAdvice {
  minSellPrice: number;
  buyPrice: number;
  networkFee: number;
  amountUsdt: number;
  sellAmountAfterFee: number;
  minMarginPct: number;
  profitAtMinUah: number;
  profitAtMinPct: number;
  /** З analyze_sell_depth */
  recommendedPrice: number;
  absoluteMinSell: number;
  competitorPrice: number;
  reason: string;
  estimatedProfit: number;
  /** Ціна, перед якою накопичена «стінка» ліквідності. */
  wallPrice: number;
  wallVolumeUsdt: number;
}

/** Вердикт ризик-движка/LLM по контрагенту. */
export type MerchantVerdict = 'OK' | 'WARN' | 'BLOCK' | 'PENDING';

/** Вхідний ордер на власне оголошення (MakerAdMonitor). */
export interface IncomingOrder {
  orderId: string;
  itemId: string;
  exchange: string;
  price: number;
  amountUsdt: number;
  totalFiat: number;
  createdAt: number;
  counterparty: {
    merchantId: string;
    merchantName: string;
    finishRate: number;
    monthOrderCount: number;
    isVerified: boolean;
    riskFlag?: string;
  };
  rec: MerchantVerdict;
  reason: string;
}

/** Стан власного оголошення. */
export interface MakerAd {
  itemId: string;
  exchange: string;
  side: MakerSide;
  bank: string;
  price: number;
  remainingUsdt: number;
  totalUsdt: number;
  isOnline: boolean;
  /** Позиція в стакані: 1 = найкраща ціна. */
  bookPosition: number;
  competitorsAhead: number;
}
