import React from 'react';
import { Wallet, ShieldCheck, Award, BarChart3, Landmark } from 'lucide-react';
import { cn } from '../lib/utils';
import { useExchangeAccounts, ExchangeAccount } from '../hooks/useExchangeAccounts';

export default function AccountsPanel() {
  // Використовуємо НАШ реальний хук, а не моковий від Bolt
  const { data: accounts, isLoading } = useExchangeAccounts();

  if (isLoading) {
    return (
      <div className="space-y-6 max-w-7xl mx-auto p-4 md:p-8">
        <AccountsSkeleton />
      </div>
    );
  }

  const safeAccounts = accounts || [];
  const totalUAH = safeAccounts.reduce((sum, acc) => sum + acc.balanceUAH, 0);
  const totalUSDT = safeAccounts.reduce((sum, acc) => sum + acc.balanceUSDT, 0);

  // Курс для відображення (можна потім теж тягнути з бекенду)
  const usdtToUahRate = 39.5;
  const totalGlobalCapital = totalUAH + (totalUSDT * usdtToUahRate);

  return (
    <div className="space-y-6 max-w-7xl mx-auto p-4 md:p-8">
      <div className="bg-gradient-to-br from-accent-500/20 via-accent-500/10 to-transparent border border-accent-500/30 rounded-3xl p-8">
        <div className="flex items-center gap-4 mb-4">
          <div className="p-3 bg-accent-500/20 rounded-2xl">
            <Landmark className="w-8 h-8 text-accent-400" />
          </div>
          <div>
            <h2 className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-1">
              Total Global Capital
            </h2>
            <div className="text-4xl font-black text-white tabular-nums">
              {totalGlobalCapital.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} <span className="text-accent-400 text-2xl">₴</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-400">Across</span>
          <span className="font-bold text-accent-400">{safeAccounts.length} exchanges</span>
          <span className="text-slate-400">• Live balances</span>

          {/* Додали відображення розбивки, як було в нашому старому дизайні */}
          <span className="text-slate-500 ml-4 font-mono">({totalUAH.toLocaleString('en-US', {maximumFractionDigits: 0})} ₴ + {totalUSDT.toLocaleString('en-US', {maximumFractionDigits: 0})} ₮)</span>
        </div>
      </div>

      {safeAccounts.length === 0 ? (
        <div className="bg-slate-900/50 border border-slate-800 rounded-3xl p-12 text-center">
          <div className="w-16 h-16 bg-slate-800 rounded-full flex items-center justify-center mx-auto mb-4">
            <Wallet className="w-8 h-8 text-slate-600" />
          </div>
          <h3 className="text-lg font-bold text-white mb-2">No Connected Accounts</h3>
          <p className="text-sm text-slate-400">
            Connect your exchange API keys to view balances and account details.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {safeAccounts.map((account) => (
            <ExchangeAccountCard key={account.id} account={account} />
          ))}
        </div>
      )}
    </div>
  );
}

const ExchangeAccountCard: React.FC<{ account: ExchangeAccount }> = ({ account }) => {
  // Адаптуємо дані нашого бекенда під дизайн Bolt
  const limitPercentage = Math.min((account.tradingVolume30d / account.volumeLimit) * 100, 100);

  return (
    <div className="bg-slate-900/80 backdrop-blur-md border border-slate-800 rounded-3xl p-6 hover:border-accent-500/30 transition-all">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ExchangeIcon name={account.exchange} />
          <div>
            <h3 className="font-bold text-white text-lg">{account.exchange}</h3>
            <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold">
              Exchange Account
            </p>
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-black text-white tabular-nums">
            {account.balanceUAH.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            <span className="text-sm text-slate-400 ml-1">₴</span>
          </div>
          <div className="text-xs text-accent-400 font-bold tabular-nums">
            {account.balanceUSDT.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} <span className="text-accent-500/70">USDT</span>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-slate-950/50 border border-slate-800 rounded-2xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <ShieldCheck className="w-4 h-4 text-blue-400" />
              <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold">
                KYC Level
              </span>
            </div>
            <div className="text-lg font-bold text-white">{account.kycLevel}</div>
          </div>

          <div className="bg-slate-950/50 border border-slate-800 rounded-2xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <Award className="w-4 h-4 text-purple-400" />
              <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold">
                Merchant
              </span>
            </div>
            <div className={cn(
              "text-sm font-bold uppercase tracking-wider",
              account.merchantStatus === 'Active' ? "text-accent-400" :
              account.merchantStatus === 'Pending' ? "text-orange-400" : "text-slate-500"
            )}>
              {account.merchantStatus}
            </div>
          </div>
        </div>

        <div className="bg-slate-950/50 border border-slate-800 rounded-2xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-accent-400" />
              <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold">
                30-Day Volume
              </span>
            </div>
            <span className="text-lg font-bold text-white tabular-nums">
              ${account.tradingVolume30d.toLocaleString('en-US')}
            </span>
          </div>

          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span className="text-slate-500">Limit Usage</span>
              <span className="text-slate-300 font-bold tabular-nums">
                {limitPercentage.toFixed(0)}%
              </span>
            </div>
            <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all duration-1000",
                  limitPercentage > 90 ? "bg-red-500" :
                  limitPercentage > 75 ? "bg-orange-500" : "bg-accent-500"
                )}
                style={{ width: `${limitPercentage}%` }}
              />
            </div>
            <div className="flex justify-between text-[10px] font-mono uppercase text-slate-500 tabular-nums">
              <span>${account.tradingVolume30d.toLocaleString('en-US')}</span>
              <span>${account.volumeLimit.toLocaleString('en-US')}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const AccountsSkeleton: React.FC = () => {
  return (
    <>
      <div className="bg-slate-900/50 border border-slate-800 rounded-3xl p-8 animate-pulse">
        <div className="flex items-center gap-4 mb-4">
          <div className="w-14 h-14 bg-slate-800 rounded-2xl" />
          <div className="space-y-2">
            <div className="h-3 w-32 bg-slate-800 rounded" />
            <div className="h-8 w-48 bg-slate-800 rounded" />
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {[1, 2].map((i) => (
          <div key={i} className="bg-slate-900 border border-slate-800 rounded-3xl p-6 animate-pulse">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-10 h-10 bg-slate-800 rounded-full" />
              <div className="space-y-2">
                <div className="h-4 w-24 bg-slate-800 rounded" />
                <div className="h-3 w-32 bg-slate-800 rounded" />
              </div>
            </div>
            <div className="space-y-4">
              <div className="h-20 bg-slate-800 rounded-2xl" />
              <div className="h-24 bg-slate-800 rounded-2xl" />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function ExchangeIcon({ name }: { name: string }) {
  const colors: Record<string, string> = {
    'Bybit': 'bg-orange-500 text-white',
    'OKX': 'bg-white text-black',
    'Binance': 'bg-yellow-400 text-black',
    'MEXC': 'bg-blue-500 text-white',
  };
  return (
    <div className={cn(
      "w-10 h-10 rounded-full flex items-center justify-center border-2 border-slate-900 font-black text-xs",
      colors[name] || 'bg-slate-700 text-white'
    )}>
      {name[0]}
    </div>
  );
}