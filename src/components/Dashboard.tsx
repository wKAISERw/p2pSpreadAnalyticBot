import React from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { TrendingUp, ArrowRightLeft, ShieldAlert, ExternalLink, AlertTriangle, Clock } from 'lucide-react';
import { ArbitrageOpportunity, Order, SystemStats } from '../types';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { formatDistanceToNow } from 'date-fns';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface DashboardProps {
  stats: SystemStats;
  opportunities: ArbitrageOpportunity[];
}

export default function Dashboard({ stats, opportunities }: DashboardProps) {
  // Використовуємо всі ордери, які прислав бекенд. 
  // Бекенд сам має вирішувати, які ордери ще активні, а які вже викуплені.
  const activeOpportunities = opportunities;

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          icon={<TrendingUp className="w-5 h-5 text-emerald-400" />}
          label="Active Spreads"
          value={activeOpportunities.length.toString()}
          subValue="Real-time opportunities"
        />
        <StatCard
          icon={<ArrowRightLeft className="w-5 h-5 text-blue-400" />}
          label="Cycles Completed"
          value={(stats?.cycles ?? 0).toLocaleString()}
          subValue={`${stats.lastCycleMs}ms avg latency`}
        />
        <StatCard
          icon={<ShieldAlert className="w-5 h-5 text-orange-400" />}
          label="Bots Detected"
          value={(stats.botsDetectedToday ?? 0).toString()}
          subValue="Today's anomalies"
        />
        <StatCard
          icon={<Clock className="w-5 h-5 text-purple-400" />}
          label="LLM Queue"
          value={(stats.llmQueue ?? 0).toString()}
          subValue={`${stats.reviewQueue} reviews pending`}
        />
      </div>

      <div className="space-y-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-lg font-semibold flex items-center gap-2 text-white">
            <TrendingUp className="w-5 h-5 text-emerald-500" />
            Live Arbitrage Opportunities
          </h2>
          <span className="text-xs text-slate-500 font-mono">Updated every 5s</span>
        </div>

        <AnimatePresence mode="popLayout">
          {activeOpportunities.length > 0 ? (
            activeOpportunities.map((opp, index) => (
                <OpportunityCard key={`${opp.buyOrder.id}-${opp.sellOrder.id}-${index}`} opp={opp} />
            ))
          ) : (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="p-12 border-2 border-dashed border-slate-800 rounded-3xl flex flex-col items-center justify-center text-slate-500"
            >
              <div className="w-12 h-12 bg-slate-900 rounded-full flex items-center justify-center mb-4">
                <TrendingUp className="w-6 h-6 opacity-20" />
              </div>
              <p>Searching for profitable spreads...</p>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, subValue }: any) {
  return (
    <div className="bg-slate-900 border border-slate-800 p-5 rounded-3xl">
      <div className="flex items-center gap-3 mb-3">
        <div className="p-2 bg-slate-800 rounded-xl">{icon}</div>
        <span className="text-xs font-bold text-slate-500 uppercase tracking-wider">{label}</span>
      </div>
      <div className="text-2xl font-bold text-white mb-1">{value}</div>
      <div className="text-[10px] text-slate-500 font-medium uppercase tracking-widest">{subValue}</div>
    </div>
  );
}

const OpportunityCard: React.FC<{ opp: ArbitrageOpportunity }> = ({ opp }) => {
  const isHighRisk = (opp.buyOrder.riskScore || 0) >= 50 || (opp.sellOrder.riskScore || 0) >= 50;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className={cn(
        "bg-slate-900 border rounded-4xl overflow-hidden transition-all hover:shadow-2xl hover:shadow-emerald-500/5",
        isHighRisk ? "border-red-500/30" : "border-slate-800"
      )}
    >
      <div className="p-6">
        <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
          <div className="flex items-center gap-4">
            <div className="flex items-center -space-x-2">
              <ExchangeIcon name={opp.buyOrder.exchange} />
              <div className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center border-2 border-slate-900 z-10">
                <ArrowRightLeft className="w-3 h-3 text-slate-400" />
              </div>
              <ExchangeIcon name={opp.sellOrder.exchange} />
            </div>
            <div>
              <div className="text-sm font-bold text-white flex items-center gap-2">
                {opp.buyOrder.exchange} → {opp.sellOrder.exchange}
                <span className={cn(
                  "text-[10px] px-2 py-0.5 rounded-full font-black uppercase tracking-tighter",
                  opp.routeType === 'CROSS' ? "bg-purple-500/20 text-purple-400" : "bg-blue-500/20 text-blue-400"
                )}>
                  {opp.routeType}
                </span>
              </div>
              <div className="text-[10px] text-slate-500 font-mono uppercase tracking-widest">
                {opp.buyBank} → {opp.sellBank}
              </div>
            </div>
          </div>

          <div className="text-right">
            <div className="text-2xl font-black text-emerald-400">+{(opp.netSpread ?? 0).toFixed(2)}%</div>
            <div className="text-xs font-bold text-emerald-500/60 uppercase tracking-widest">Profit: {(opp.netProfit ?? 0).toFixed(0)} ₴</div>          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <OrderDetails side="BUY" order={opp.buyOrder} />
          <OrderDetails side="SELL" order={opp.sellOrder} />
        </div>
      </div>

      <div className="bg-slate-800/30 px-6 py-4 flex items-center justify-between border-t border-slate-800">
        <div className="flex items-center gap-4">
          <div className="text-xs font-mono">
            <span className="text-slate-500">DEAL:</span> <span className="text-white font-bold">{(opp.dealAmount ?? 0).toFixed(0)} ₴</span>
          </div>
          <div className="text-[10px] font-mono text-slate-500 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {opp.timestamp
              ? formatDistanceToNow(new Date(opp.timestamp), { addSuffix: true })
              : 'щойно'}
          </div>
        </div>
        <div className="flex gap-2">
          <a
            href={opp.buyOrder.link}
            target="_blank"
            rel="noreferrer"
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-colors flex items-center gap-2"
          >
            BUY <ExternalLink className="w-3 h-3" />
          </a>
          <a
            href={opp.sellOrder.link}
            target="_blank"
            rel="noreferrer"
            className="px-4 py-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold rounded-xl transition-colors flex items-center gap-2 shadow-lg shadow-emerald-500/20"
          >
            SELL <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </div>
    </motion.div>
  );
}

function OrderDetails({ side, order }: { side: 'BUY' | 'SELL', order: Order }) {
  const isRisk = (order.riskScore || 0) > 0;
  return (
    <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
      <div className="flex items-center justify-between mb-3">
        <span className={cn(
          "text-[10px] font-black px-2 py-0.5 rounded-md tracking-tighter",
          side === 'BUY' ? "bg-blue-500/20 text-blue-400" : "bg-emerald-500/20 text-emerald-400"
        )}>{side}</span>
        <div className="flex items-center gap-1 text-[10px] font-mono text-slate-500">
          <span>{order.orderCount} orders</span>
          <span>•</span>
         <span>{(order.finishRate ?? 0).toFixed(1)}%</span>
        </div>
      </div>

      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-bold text-white truncate max-w-30">{order.merchantName}</div>
        <div className="text-lg font-black text-white">{(order.price ?? 0).toFixed(2)}</div>
      </div>

      <div className="text-[10px] text-slate-500 mb-3 font-mono">
        Limits: {order.minLimit} - {order.maxLimit} ₴
      </div>

      {isRisk && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          <div className={cn(
            "flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-tight border",
            (order.riskScore || 0) >= 50 
              ? "bg-red-500/10 text-red-400 border-red-500/20" 
              : "bg-orange-500/10 text-orange-400 border-orange-500/20"
          )}>
            <AlertTriangle className="w-2.5 h-2.5" />
            SCORE: {order.riskScore}
          </div>
          {order.riskFlag && order.riskFlag !== 'OK' && order.riskFlag.split(/[:,]/).map((flag, idx) => {
            const trimmed = flag.trim();
            if (!trimmed) return null;
            return (
              <div key={idx} className={cn(
                "flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-tight border",
                (order.riskScore || 0) >= 50 
                  ? "bg-red-500/10 text-red-400 border-red-500/20" 
                  : "bg-orange-500/10 text-orange-400 border-orange-500/20"
              )}>
                {trimmed}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ExchangeIcon({ name }: { name: string }) {
  const colors: Record<string, string> = {
    'Bybit': 'bg-orange-500',
    'OKX': 'bg-white',
    'Binance': 'bg-yellow-400',
    'MEXC': 'bg-blue-500'
  };
  return (
    <div className={cn("w-8 h-8 rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-[10px] text-slate-950", colors[name] || 'bg-slate-700')}>
      {name[0]}
    </div>
  );
}
