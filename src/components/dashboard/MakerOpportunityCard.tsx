import React from 'react';
import {motion} from 'motion/react';
import {
    ArrowRightLeft,
    ExternalLink,
    Copy,
    Clock,
    TrendingUp,
    TrendingDown,
    Users,
    Layers
} from 'lucide-react';
import {cn} from '../../lib/utils';
import {toast} from 'sonner';
import {formatDistanceToNow} from 'date-fns';
import {MakerOpportunity, CompetitorOrder} from '../../types/maker';

const BANK_NAMES_MAP: Record<string, string> = {
    "43": "Monobank",
    "14": "PrivatBank",
    "64": "ПУМБ",
    "48": "А-Банк",
    "99": "Ощадбанк",
    "380": "Raiffeisen",
    "328": "Sense",
    "319": "OTP",
    "553": "izibank",
    "transfer": "Global Transfer"
};

const getBankName = (code: string) => BANK_NAMES_MAP[code] || code;

interface MakerOpportunityCardProps {
    opportunity: MakerOpportunity,
    viewMode?: 'detailed' | 'compact',
    key?: unknown
}

export const MakerOpportunityCard: React.FC<MakerOpportunityCardProps> = ({ opportunity, viewMode }) => {
    const handleCopy = (text: string | number, label: string) => {
        navigator.clipboard.writeText(text.toString());
        toast.success(`Copied ${label}!`);
    };

    const variants = {
        hidden: {opacity: 0, y: 20},
        show: {opacity: 1, y: 0}
    };

    if (viewMode === 'compact') {
        return (
            <MakerCompactCard
                opportunity={opportunity}
                onCopy={handleCopy}
                variants={variants}
            />
        );
    }

    return (
        <motion.div
            layout
            variants={variants}
            initial="hidden"
            animate="show"
            exit={{opacity: 0, scale: 0.95}}
            transition={{type: "spring", stiffness: 300, damping: 30}}
            className="bg-slate-900/80 backdrop-blur-md rounded-3xl overflow-hidden transition-all hover:scale-[1.01] hover:shadow-[0_4px_20px_rgba(168,85,247,0.1)] border border-purple-500/30 shadow-lg relative"
        >
            {/* Header */}
            <div className="p-4 md:p-6">
                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
                    <div className="flex items-center gap-4">
                        <div className="flex items-center -space-x-2">
                            <ExchangeIcon name={opportunity.exchange}/>
                            <div
                                className="w-8 h-8 rounded-full bg-purple-500/20 flex items-center justify-center border-2 border-slate-900 z-10">
                                <Layers className="w-3 h-3 text-purple-400"/>
                            </div>
                        </div>
                        <div>
                            <div className="text-sm font-bold text-white flex items-center gap-2">
                                {opportunity.exchange}
                                <span
                                    className="text-xs px-2 py-0.5 rounded-full font-black uppercase tracking-tighter bg-purple-500/20 text-purple-400">
                  MAKER
                </span>
                            </div>
                            <div className="text-xs text-slate-400 font-mono uppercase tracking-widest">
                                {getBankName(opportunity.bank)} • {opportunity.side}
                            </div>
                        </div>
                    </div>

                    <div className="text-left md:text-right border-t border-slate-800/50 md:border-none pt-3 md:pt-0">
                        <div className="text-2xl font-black text-purple-400 tabular-nums">
                            {opportunity.suggestedPrice.toFixed(2)} ₴
                        </div>
                        <div className="text-xs font-bold text-purple-500/60 uppercase tracking-widest">
                            Suggested Price
                        </div>
                    </div>
                </div>

                {/* Order Book Depth */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                    <OrderBookPanel
                        title="Competitors"
                        orders={opportunity.competitors}
                        suggestedPrice={opportunity.suggestedPrice}
                        side={opportunity.side}
                        onCopy={handleCopy}
                    />
                    <MarketDepthPanel
                        depth={opportunity.marketDepth}
                        totalVolume={opportunity.totalVolume}
                    />
                </div>

                {/* Spread Analysis */}
                <SpreadAnalysisPanel
                    spread={opportunity.spreadFromBest}
                    avgSpread={opportunity.avgSpread}
                    marketCondition={opportunity.marketCondition}
                />
            </div>

            {/* Footer */}
            <div
                className="bg-slate-800/30 px-4 md:px-6 py-4 flex flex-col md:flex-row md:items-center justify-between border-t border-slate-800 gap-4 md:gap-0">
                <div className="flex items-center justify-between md:justify-start gap-4 w-full md:w-auto">
                    <div className="text-xs font-mono text-slate-400 flex items-center gap-1">
                        <Users className="w-3 h-3"/>
                        <span>{opportunity.competitors.length} competitors</span>
                    </div>
                    <div className="text-xs font-mono text-slate-400 flex items-center gap-1">
                        <Clock className="w-3 h-3"/>
                        {formatDistanceToNow(new Date(opportunity.timestamp), {addSuffix: true})}
                    </div>
                </div>
                <div className="flex gap-2 w-full md:w-auto">
                    <motion.button
                        whileHover={{scale: 1.02}}
                        whileTap={{scale: 0.95}}
                        onClick={() => handleCopy(opportunity.suggestedPrice.toFixed(2), 'Price')}
                        className="flex-1 md:flex-none justify-center px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-colors flex items-center gap-2"
                    >
                        <Copy className="w-3 h-3"/> Copy Price
                    </motion.button>
                    <motion.a
                        whileHover={{scale: 1.02}}
                        whileTap={{scale: 0.95}}
                        href={opportunity.createAdLink}
                        target="_blank"
                        rel="noreferrer"
                        className="flex-1 md:flex-none justify-center px-4 py-2 bg-purple-500 hover:bg-purple-400 text-white text-xs font-bold rounded-xl transition-colors flex items-center gap-2 shadow-lg shadow-purple-500/20"
                    >
                        Create Ad <ExternalLink className="w-3 h-3"/>
                    </motion.a>
                </div>
            </div>

            {/* Progress bar */}
            <div className="h-1 w-full bg-slate-800 absolute bottom-0 left-0">
                <motion.div
                    className="h-full bg-purple-500"
                    initial={{width: "100%"}}
                    animate={{width: "0%"}}
                    transition={{duration: 120, ease: "linear"}}
                />
            </div>
        </motion.div>
    );
}

// Compact card variant
function MakerCompactCard({
                              opportunity,
                              onCopy,
                              variants
                          }: {
    opportunity: MakerOpportunity;
    onCopy: (text: string | number, label: string) => void;
    variants: any;
}) {
    return (
        <motion.div
            layout
            variants={variants}
            initial="hidden"
            animate="show"
            exit={{opacity: 0, scale: 0.95}}
            transition={{type: "spring", stiffness: 300, damping: 30}}
            className="bg-slate-900/80 backdrop-blur-md overflow-hidden transition-all hover:scale-[1.01] border border-purple-500/30 shadow-lg relative rounded-xl"
        >
            <div className="p-3 flex items-center justify-between gap-4">
                <div className="flex items-center gap-3 flex-1">
                    <ExchangeIcon name={opportunity.exchange} size="sm"/>
                    <div className="flex items-center gap-2 text-sm font-bold text-white tabular-nums">
            <span
                className="cursor-pointer hover:text-purple-400 transition-colors"
                onClick={() => onCopy(opportunity.suggestedPrice, 'Price')}
            >
              {opportunity.suggestedPrice.toFixed(2)} ₴
            </span>
                        <span className="text-xs text-slate-400 font-mono uppercase tracking-widest">
              {getBankName(opportunity.bank)}
            </span>
                    </div>
                </div>

                <div className="flex-1 text-center">
          <span className="text-xs font-medium text-slate-400 uppercase tracking-widest tabular-nums">
            {opportunity.competitors.length} competitors
          </span>
                </div>

                <div className="flex items-center gap-4 justify-end flex-1">
                    <div className="text-right">
                        <div className={cn(
                            "text-sm font-black tabular-nums",
                            opportunity.spreadFromBest > 0 ? "text-emerald-400" : "text-red-400"
                        )}>
                            {opportunity.spreadFromBest > 0 ? '+' : ''}{opportunity.spreadFromBest.toFixed(2)}%
                        </div>
                        <div className="text-xs font-bold text-slate-500 uppercase tracking-widest">
                            vs best
                        </div>
                    </div>
                    <motion.a
                        whileHover={{scale: 1.1}}
                        whileTap={{scale: 0.9}}
                        href={opportunity.createAdLink}
                        target="_blank"
                        rel="noreferrer"
                        className="p-1.5 bg-purple-500 hover:bg-purple-400 text-white rounded-lg transition-colors"
                    >
                        <ExternalLink className="w-3 h-3"/>
                    </motion.a>
                </div>
            </div>
        </motion.div>
    );
}

// Order book panel showing competitors
function OrderBookPanel({
                            title,
                            orders,
                            suggestedPrice,
                            side,
                            onCopy
                        }: {
    title: string;
    orders: CompetitorOrder[];
    suggestedPrice: number;
    side: 'BUY' | 'SELL';
    onCopy: (text: string | number, label: string) => void;
}) {
    const sortedOrders = [...orders].sort((a, b) =>
        side === 'BUY' ? b.price - a.price : a.price - b.price
    ).slice(0, 5);

    const maxVolume = Math.max(...sortedOrders.map(o => o.availableAmount));

    return (
        <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
            <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">{title}</span>
                <span className="text-xs font-bold text-purple-400 tabular-nums">{orders.length} ads</span>
            </div>

            <div className="space-y-1.5">
                {sortedOrders.map((order, idx) => {
                    const volumePercent = (order.availableAmount / maxVolume) * 100;
                    const isBetterPrice = side === 'BUY'
                        ? order.price < suggestedPrice
                        : order.price > suggestedPrice;

                    return (
                        <div
                            key={order.merchantId + idx}
                            className="relative flex items-center justify-between py-1.5 px-2 rounded-lg overflow-hidden group cursor-pointer hover:bg-slate-800/50 transition-colors"
                            onClick={() => onCopy(order.price, 'Competitor Price')}
                        >
                            {/* Volume bar background */}
                            <div
                                className={cn(
                                    "absolute inset-y-0 left-0 opacity-20",
                                    isBetterPrice ? "bg-emerald-500" : "bg-slate-700"
                                )}
                                style={{width: `${volumePercent}%`}}
                            />

                            <div className="relative flex items-center gap-2">
                <span className={cn(
                    "text-xs font-bold tabular-nums",
                    isBetterPrice ? "text-emerald-400" : "text-white"
                )}>
                  {order.price.toFixed(2)}
                </span>
                                {isBetterPrice && (
                                    side === 'BUY'
                                        ? <TrendingDown className="w-3 h-3 text-emerald-400"/>
                                        : <TrendingUp className="w-3 h-3 text-emerald-400"/>
                                )}
                            </div>

                            <div className="relative flex items-center gap-2">
                <span className="text-xs text-slate-400 truncate max-w-[80px]">
                  {order.merchantName}
                </span>
                                <span className="text-xs font-mono text-slate-500 tabular-nums">
                  {(order.availableAmount / 1000).toFixed(0)}k
                </span>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// Market depth visualization
function MarketDepthPanel({
                              depth,
                              totalVolume
                          }: {
    depth: { price: number; volume: number }[];
    totalVolume: number;
}) {
    const maxVol = Math.max(...depth.map(d => d.volume));

    return (
        <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
            <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Market Depth</span>
                <span className="text-xs font-bold text-slate-400 tabular-nums">
          {(totalVolume / 1000000).toFixed(1)}M ₴
        </span>
            </div>

            <div className="flex items-end gap-1 h-20">
                {depth.map((d, idx) => (
                    <div key={idx} className="flex-1 flex flex-col items-center gap-1">
                        <motion.div
                            className="w-full bg-purple-500/30 rounded-t"
                            initial={{height: 0}}
                            animate={{height: `${(d.volume / maxVol) * 100}%`}}
                            transition={{delay: idx * 0.05, duration: 0.3}}
                        />
                        <span className="text-[9px] text-slate-500 tabular-nums">
              {d.price.toFixed(1)}
            </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// Spread analysis panel
function SpreadAnalysisPanel({
                                 spread,
                                 avgSpread,
                                 marketCondition
                             }: {
    spread: number;
    avgSpread: number;
    marketCondition: 'favorable' | 'neutral' | 'competitive';
}) {
    const conditionColors = {
        favorable: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30',
        neutral: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
        competitive: 'text-red-400 bg-red-500/10 border-red-500/30',
    };

    return (
        <div
            className="bg-slate-950/30 rounded-xl p-3 flex flex-wrap items-center justify-between gap-3 border border-slate-800/30">
            <div className="flex items-center gap-4">
                <div>
                    <span className="text-xs text-slate-500 uppercase tracking-widest">Spread vs Best</span>
                    <div className={cn(
                        "text-lg font-black tabular-nums",
                        spread > 0 ? "text-emerald-400" : spread < 0 ? "text-red-400" : "text-slate-400"
                    )}>
                        {spread > 0 ? '+' : ''}{spread.toFixed(2)}%
                    </div>
                </div>
                <div className="h-8 w-px bg-slate-800"/>
                <div>
                    <span className="text-xs text-slate-500 uppercase tracking-widest">Avg Spread</span>
                    <div className="text-lg font-black text-slate-300 tabular-nums">
                        {avgSpread.toFixed(2)}%
                    </div>
                </div>
            </div>
            <div className={cn(
                "px-3 py-1.5 rounded-xl text-xs font-bold uppercase tracking-wider border",
                conditionColors[marketCondition]
            )}>
                {marketCondition}
            </div>
        </div>
    );
}

function ExchangeIcon({name, size = 'md'}: { name: string; size?: 'sm' | 'md' }) {
    const colors: Record<string, string> = {
        'Bybit': 'bg-orange-500',
        'OKX': 'bg-white',
        'Binance': 'bg-yellow-400',
        'MEXC': 'bg-blue-500'
    };
    return (
        <div className={cn(
            "rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-slate-950",
            colors[name] || 'bg-slate-700',
            size === 'sm' ? "w-6 h-6 text-[10px]" : "w-8 h-8 text-xs"
        )}>
            {name[0]}
        </div>
    );
}
