import React, { useState, useMemo } from 'react';
import { motion } from 'motion/react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, Legend } from 'recharts';
import { useAppStore } from '../store';
import { TrendingUp, Activity, DollarSign, Target } from 'lucide-react';
import { cn } from '../lib/utils';

// Generate realistic mock data for the last 30 days
const generateMockData = () => {
  const data = [];
  let currentCapital = 50000;
  
  for (let i = 30; i >= 0; i--) {
    const date = new Date();
    date.setDate(date.getDate() - i);
    
    const dailyProfit = Math.random() * 500 + 100; // 100 to 600 UAH
    const volume = Math.random() * 20000 + 5000; // 5k to 25k UAH
    const trades = Math.floor(Math.random() * 15) + 2;
    
    currentCapital += dailyProfit;
    
    data.push({
      date: date.toLocaleDateString('uk-UA', { month: 'short', day: 'numeric' }),
      profit: Math.round(dailyProfit),
      capital: Math.round(currentCapital),
      volume: Math.round(volume),
      trades
    });
  }
  return data;
};

export function AnalyticsPanel() {
  const [timeframe, setTimeframe] = useState<'7d' | '14d' | '30d'>('14d');
  
  const mockData = useMemo(() => generateMockData(), []);
  
  const filteredData = useMemo(() => {
    const days = timeframe === '7d' ? 7 : timeframe === '14d' ? 14 : 30;
    return mockData.slice(-days);
  }, [mockData, timeframe]);

  const totalProfit = filteredData.reduce((sum, day) => sum + day.profit, 0);
  const totalVolume = filteredData.reduce((sum, day) => sum + day.volume, 0);
  const totalTrades = filteredData.reduce((sum, day) => sum + day.trades, 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Analytics & PnL</h1>
          <p className="text-sm text-zinc-400">Track your arbitrage performance over time</p>
        </div>
        
        <div className="flex items-center gap-2 bg-zinc-900/50 p-1 rounded-lg border border-zinc-800/50">
          {(['7d', '14d', '30d'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTimeframe(t)}
              className={cn(
                "px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                timeframe === t 
                  ? "bg-zinc-800 text-zinc-100 shadow-sm" 
                  : "text-zinc-400 hover:text-zinc-200"
              )}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard 
          title="Total Profit" 
          value={`+${totalProfit.toLocaleString('uk-UA')} ₴`} 
          icon={<TrendingUp className="h-5 w-5 text-emerald-400" />} 
          trend="+12.5%" 
        />
        <StatCard 
          title="Trading Volume" 
          value={`${totalVolume.toLocaleString('uk-UA')} ₴`} 
          icon={<DollarSign className="h-5 w-5 text-blue-400" />} 
          trend="+5.2%" 
        />
        <StatCard 
          title="Total Trades" 
          value={totalTrades.toString()} 
          icon={<Activity className="h-5 w-5 text-purple-400" />} 
        />
        <StatCard 
          title="Avg. Profit / Trade" 
          value={`${Math.round(totalProfit / totalTrades)} ₴`} 
          icon={<Target className="h-5 w-5 text-amber-400" />} 
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main PnL Chart */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="lg:col-span-2 bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-6"
        >
          <h3 className="text-sm font-medium text-zinc-400 mb-6">Cumulative Capital Growth</h3>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={filteredData} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorCapital" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                <XAxis 
                  dataKey="date" 
                  stroke="#52525b" 
                  fontSize={12} 
                  tickLine={false} 
                  axisLine={false}
                  dy={10}
                />
                <YAxis 
                  stroke="#52525b" 
                  fontSize={12} 
                  tickLine={false} 
                  axisLine={false}
                  tickFormatter={(val) => `${val / 1000}k`}
                  dx={-10}
                />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', borderRadius: '8px' }}
                  itemStyle={{ color: '#e4e4e7' }}
                />
                <Area 
                  type="monotone" 
                  dataKey="capital" 
                  stroke="#10b981" 
                  strokeWidth={2}
                  fillOpacity={1} 
                  fill="url(#colorCapital)" 
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </motion.div>

        {/* Daily Profit Bar Chart */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-6"
        >
          <h3 className="text-sm font-medium text-zinc-400 mb-6">Daily Profit (UAH)</h3>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={filteredData} margin={{ top: 5, right: 0, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
                <XAxis 
                  dataKey="date" 
                  stroke="#52525b" 
                  fontSize={12} 
                  tickLine={false} 
                  axisLine={false}
                  dy={10}
                />
                <Tooltip 
                  cursor={{ fill: '#27272a', opacity: 0.4 }}
                  contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', borderRadius: '8px' }}
                />
                <Bar 
                  dataKey="profit" 
                  fill="#3b82f6" 
                  radius={[4, 4, 0, 0]} 
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </motion.div>
      </div>
    </div>
  );
}

function StatCard({ title, value, icon, trend }: { title: string, value: string, icon: React.ReactNode, trend?: string }) {
  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5 flex flex-col"
    >
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm font-medium text-zinc-400">{title}</span>
        <div className="p-2 bg-zinc-800/50 rounded-lg">
          {icon}
        </div>
      </div>
      <div className="flex items-end justify-between mt-auto">
        <span className="text-2xl font-bold text-zinc-100 tabular-nums">{value}</span>
        {trend && (
          <span className="text-xs font-medium text-emerald-400 bg-emerald-400/10 px-2 py-1 rounded-md">
            {trend}
          </span>
        )}
      </div>
    </motion.div>
  );
}
