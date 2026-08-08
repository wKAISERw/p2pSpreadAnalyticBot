import React from 'react';
import { Link } from 'react-router-dom';
import { Wallet, ShieldCheck, Award, BarChart3, Landmark, AlertTriangle } from 'lucide-react';
import { cn } from '../lib/utils';
import { useExchangeAccounts, ExchangeAccount } from '../hooks/useExchangeAccounts';

const num = (v: number) =>
  v.toLocaleString('uk-UA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

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
  const failed = safeAccounts.filter(acc => acc.error);

  return (
    <div className="space-y-6 max-w-7xl mx-auto p-4 md:p-8">
      {/*
        Дві валюти показуються окремо.

        Тут раніше був «Total Global Capital» — сума гривні й USDT за курсом
        39.5, вписаним у код. Курс жодного разу не оновлювався і ні з чим не
        звірявся, тобто головна цифра сторінки була вигадана. Складати їх
        нема на чому: курсу USDT/UAH бекенд не віддає.
      */}
      <div className="bg-gradient-to-br from-accent-500/20 via-accent-500/10 to-transparent border border-accent-500/30 rounded-3xl p-8">
        <div className="flex items-center gap-4 mb-4">
          <div className="p-3 bg-accent-500/20 rounded-2xl">
            <Landmark className="w-8 h-8 text-accent-400" />
          </div>
          <div>
            <h2 className="text-xs uppercase tracking-widest text-slate-400 font-semibold mb-1">
              Баланси на біржах
            </h2>
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <div className="text-4xl font-black text-white tabular-nums">
                {num(totalUSDT)} <span className="text-accent-400 text-2xl">USDT</span>
              </div>
              <div className="text-2xl font-bold text-slate-300 tabular-nums">
                {num(totalUAH)} <span className="text-slate-500 text-lg">₴</span>
              </div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-slate-400">Підключено бірж:</span>
          <span className="font-bold text-accent-400">{safeAccounts.length}</span>
          {failed.length > 0 && (
            <span className="flex items-center gap-1.5 text-orange-400 ml-2">
              <AlertTriangle className="w-3.5 h-3.5" />
              {failed.length} не відповіли — суми неповні
            </span>
          )}
        </div>
      </div>

      {safeAccounts.length === 0 ? (
        <div className="bg-slate-900/50 border border-slate-800 rounded-3xl p-12 text-center">
          <div className="w-16 h-16 bg-slate-800 rounded-full flex items-center justify-center mx-auto mb-4">
            <Wallet className="w-8 h-8 text-slate-600" />
          </div>
          <h3 className="text-lg font-bold text-white mb-2">Жодної біржі не підключено</h3>
          <p className="text-sm text-slate-400">
            Додай ключі в розділі{' '}
            <Link to="/app/apikeys" className="text-accent-400 hover:text-accent-300 underline">
              «Ключі бірж»
            </Link>
            {' '}— після цього тут з'являться баланси.
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
  return (
    <div className={cn(
      "bg-slate-900/80 backdrop-blur-md border rounded-3xl p-6 transition-all",
      account.error ? "border-orange-500/30" : "border-slate-800 hover:border-accent-500/30"
    )}>
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <ExchangeIcon name={account.exchange} />
          <div>
            <h3 className="font-bold text-white text-lg">{account.exchange}</h3>
            <p className="text-xs text-slate-400 uppercase tracking-widest font-semibold">
              Акаунт біржі
            </p>
          </div>
        </div>

        {account.error ? (
          <div className="text-right max-w-[14rem]">
            <div className="flex items-center justify-end gap-1.5 text-orange-400 text-sm font-bold">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              Немає відповіді
            </div>
            <div className="text-[11px] text-slate-500 truncate" title={account.error}>
              {account.error}
            </div>
          </div>
        ) : (
          <div className="text-right">
            <div className="text-2xl font-black text-white tabular-nums">
              {num(account.balanceUSDT)}
              <span className="text-sm text-slate-400 ml-1">USDT</span>
            </div>
            <div className="text-xs text-slate-400 font-bold tabular-nums">
              {num(account.balanceUAH)} ₴
            </div>
          </div>
        )}
      </div>

      {/* Показуємо лише те, що біржа справді віддала. Порожні плитки з
          «Verified / None / $0» виглядали як факти про акаунт, хоча були
          константами в коді бекенда. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <InfoTile
          icon={<ShieldCheck className="w-4 h-4 text-blue-400" />}
          label="Рівень KYC"
          value={account.kycLevel}
        />
        <InfoTile
          icon={<Award className="w-4 h-4 text-purple-400" />}
          label="Мерчант"
          value={
            account.merchantStatus === 'Active' ? 'Активний'
              : account.merchantStatus === 'Pending' ? 'На розгляді'
              : account.merchantStatus === 'None' ? 'Немає статусу'
              : null
          }
          tone={
            account.merchantStatus === 'Active' ? 'text-accent-400'
              : account.merchantStatus === 'Pending' ? 'text-orange-400'
              : undefined
          }
        />
        {account.tradingVolume30d !== null && (
          <div className="sm:col-span-2">
            <InfoTile
              icon={<BarChart3 className="w-4 h-4 text-accent-400" />}
              label="Обіг за 30 днів"
              value={`${num(account.tradingVolume30d)} ₴`}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function InfoTile({
  icon, label, value, tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | null;
  tone?: string;
}) {
  return (
    <div className="bg-slate-950/50 border border-slate-800 rounded-2xl p-4">
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-xs uppercase tracking-widest text-slate-400 font-semibold">
          {label}
        </span>
      </div>
      <div className={cn('text-sm font-bold', value ? (tone ?? 'text-white') : 'text-slate-600')}>
        {value ?? 'біржа не віддає'}
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