import React from 'react';
import { motion } from 'motion/react';
import { Wallet, ShieldCheck, Activity, Landmark } from 'lucide-react';
import { useExchangeAccounts, ExchangeAccount } from '../hooks/useExchangeAccounts';
import { cn } from '../lib/utils';

export default function AccountsPanel() {
  const { data: accounts, isLoading } = useExchangeAccounts();

  const totalUAH = accounts?.reduce((sum, acc) => sum + acc.balanceUAH, 0) || 0;
  const totalUSDT = accounts?.reduce((sum, acc) => sum + acc.balanceUSDT, 0) || 0;
  
  // Mock conversion rate for display purposes
  const usdtToUahRate = 39.5;
  const totalGlobalCapital = totalUAH + (totalUSDT * usdtToUahRate);

  return (
    <div className="space-y-6 max-w-7xl mx-auto p-4 md:p-8">
      {/* Top Section: Global Capital */}
      <div className="bg-slate-900/80 backdrop-blur-md border border-slate-800 rounded-3xl p-6 md:p-8">
        <div className="flex items-center gap-4 mb-6">
          <div className="p-3 bg-emerald-500/10 rounded-2xl">
            <Landmark className="w-8 h-8 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-1">Total Global Capital</h2>
            {isLoading ? (
              <div className="h-10 w-64 bg-slate-800 animate-pulse rounded-lg"></div>
            ) : (
              <div className="text-4xl md:text-5xl font-black text-white tabular-nums tracking-tight">
                {totalGlobalCapital.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} <span className="text-emerald-500 text-2xl md:text-3xl">UAH</span>
              </div>
            )}
          </div>
        </div>
        
        <div className="flex gap-8 border-t border-slate-800/50 pt-6">
           <div>
             <div className="text-xs uppercase tracking-widest text-slate-500 font-semibold mb-1">Total UAH</div>
             {isLoading ? (
               <div className="h-7 w-32 bg-slate-800 animate-pulse rounded-md"></div>
             ) : (
               <div className="text-xl font-bold text-slate-300 tabular-nums">{totalUAH.toLocaleString('en-US', { minimumFractionDigits: 2 })} ₴</div>
             )}
           </div>
           <div>
             <div className="text-xs uppercase tracking-widest text-slate-500 font-semibold mb-1">Total USDT</div>
             {isLoading ? (
               <div className="h-7 w-32 bg-slate-800 animate-pulse rounded-md"></div>
             ) : (
               <div className="text-xl font-bold text-slate-300 tabular-nums">{totalUSDT.toLocaleString('en-US', { minimumFractionDigits: 2 })} ₮</div>
             )}
           </div>
        </div>
      </div>

      {/* Grid Section: Connected Exchanges */}
      <div>
        <h3 className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-4 flex items-center gap-2">
          <Wallet className="w-4 h-4" /> Connected Exchanges
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {isLoading ? (
            <>
              <AccountSkeleton />
              <AccountSkeleton />
              <AccountSkeleton />
            </>
          ) : (
            accounts?.map((account) => (
              <AccountCard key={account.id} account={account} />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function AccountCard({ account }: { account: ExchangeAccount; key?: React.Key }) {
  const volumePercent = Math.min((account.tradingVolume30d / account.volumeLimit) * 100, 100);

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/80 backdrop-blur-md border border-slate-800 rounded-3xl p-6 hover:border-slate-700 transition-colors"
    >
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ExchangeIcon name={account.exchange} />
          <h4 className="font-bold text-lg text-white">{account.exchange}</h4>
        </div>
        <div className={cn(
          "px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-widest",
          account.merchantStatus === 'Active' ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" :
          account.merchantStatus === 'Pending' ? "bg-orange-500/10 text-orange-400 border border-orange-500/20" :
          "bg-slate-800 text-slate-400 border border-slate-700"
        )}>
          {account.merchantStatus === 'Active' ? 'Merchant' : account.merchantStatus}
        </div>
      </div>

      <div className="space-y-4 mb-6">
        <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
          <div className="text-xs uppercase tracking-widest text-slate-500 font-semibold mb-2">Available Balance</div>
          <div className="flex justify-between items-end">
            <div className="text-xl font-black text-white tabular-nums">
              {account.balanceUAH.toLocaleString('en-US', { minimumFractionDigits: 2 })} <span className="text-sm text-slate-500">UAH</span>
            </div>
            <div className="text-md font-bold text-emerald-400 tabular-nums">
              {account.balanceUSDT.toLocaleString('en-US', { minimumFractionDigits: 2 })} <span className="text-xs text-emerald-500/70">USDT</span>
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-5">
        <div className="flex items-center justify-between text-sm">
          <div className="flex items-center gap-2 text-slate-400">
            <ShieldCheck className="w-4 h-4" />
            <span className="text-xs uppercase tracking-widest font-semibold">KYC Level</span>
          </div>
          <span className="font-bold text-slate-200 text-xs">{account.kycLevel}</span>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2 text-slate-400">
              <Activity className="w-4 h-4" />
              <span className="text-xs uppercase tracking-widest font-semibold">30D Volume</span>
            </div>
            <span className="font-bold text-slate-200 tabular-nums">${account.tradingVolume30d.toLocaleString()}</span>
          </div>
          
          <div className="relative h-1.5 bg-slate-800 rounded-full overflow-hidden">
            <div 
              className={cn(
                "absolute top-0 left-0 h-full rounded-full transition-all duration-1000",
                volumePercent > 90 ? "bg-red-500" : volumePercent > 75 ? "bg-orange-500" : "bg-emerald-500"
              )}
              style={{ width: `${volumePercent}%` }}
            />
          </div>
          <div className="text-right text-[10px] text-slate-500 font-mono uppercase">
            Limit: ${account.volumeLimit.toLocaleString()}
          </div>
        </div>
      </div>
    </motion.div>
  );
}

const AccountSkeleton: React.FC = () => {
  return (
    <div className="bg-slate-900/80 backdrop-blur-md border border-slate-800 rounded-3xl p-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-slate-800 animate-pulse"></div>
          <div className="h-6 w-24 bg-slate-800 animate-pulse rounded"></div>
        </div>
        <div className="h-6 w-20 bg-slate-800 animate-pulse rounded-lg"></div>
      </div>
      <div className="h-24 bg-slate-800/50 animate-pulse rounded-2xl mb-6"></div>
      <div className="space-y-5">
        <div className="flex justify-between">
          <div className="h-4 w-24 bg-slate-800 animate-pulse rounded"></div>
          <div className="h-4 w-32 bg-slate-800 animate-pulse rounded"></div>
        </div>
        <div className="space-y-2">
          <div className="flex justify-between">
            <div className="h-4 w-24 bg-slate-800 animate-pulse rounded"></div>
            <div className="h-4 w-20 bg-slate-800 animate-pulse rounded"></div>
          </div>
          <div className="h-1.5 w-full bg-slate-800 animate-pulse rounded-full"></div>
        </div>
      </div>
    </div>
  );
}

function ExchangeIcon({ name }: { name: string }) {
  const colors: Record<string, string> = {
    'Bybit': 'bg-orange-500',
    'OKX': 'bg-white',
    'Binance': 'bg-yellow-400',
    'MEXC': 'bg-blue-500',
  };
  return (
    <div className={cn("w-10 h-10 rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-xs text-slate-950", colors[name] || 'bg-slate-700 text-white')}>
      {name[0]}
    </div>
  );
}
