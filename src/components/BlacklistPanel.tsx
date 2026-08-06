import React, { useState } from 'react';
import { ShieldBan, Search, Trash2, Plus } from 'lucide-react';
import { BlacklistEntry } from '../types';
import { cn } from '../lib/utils';
import { motion } from 'motion/react';
import useSWR from 'swr';
import { api } from '../services/api';

export default function BlacklistPanel() {
  const [searchTerm, setSearchTerm] = useState('');
  const { data: blacklist = [], mutate } = useSWR('/blacklist', api.getBlacklist);

  const filteredList = blacklist.filter(entry => 
    entry.merchantName.toLowerCase().includes(searchTerm.toLowerCase()) ||
    entry.merchantId.toLowerCase().includes(searchTerm.toLowerCase()) ||
    entry.reason.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const handleUnban = async (merchantId: string, exchange: string) => {
    const success = await api.removeFromBlacklist(merchantId, exchange);
    if (success) {
      mutate(blacklist.filter(entry => !(entry.merchantId === merchantId && entry.exchange === exchange)), false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-red-500/10 border border-red-500/20 rounded-3xl p-6 mb-8">
        <div className="flex items-start gap-4">
          <div className="p-2 bg-red-500/20 rounded-xl">
            <ShieldBan className="w-6 h-6 text-red-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-red-400 mb-1">Global Blacklist</h2>
            <p className="text-sm text-red-500/80 leading-relaxed">
              Merchants blocked manually via Telegram or by the Risk Engine. 
              These merchants will be completely ignored by the scanner and auto-trader.
            </p>
          </div>
        </div>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-[2rem] overflow-hidden">
        <div className="p-6 border-b border-slate-800 flex items-center justify-between">
          <div className="relative w-full max-w-md">
            <Search className="absolute left-4 top-3 w-5 h-5 text-slate-500" />
            <input
              type="text"
              placeholder="Search by name, ID, or reason..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl pl-12 pr-4 py-2.5 text-sm text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all"
            />
          </div>
          <motion.button 
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
            className="flex items-center gap-2 px-4 py-2.5 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-all focus:ring-2 focus:ring-slate-500/50 outline-none"
          >
            <Plus className="w-4 h-4" />
            MANUAL BAN
          </motion.button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-950/50 border-b border-slate-800 text-xs uppercase tracking-widest text-slate-400">
                <th className="p-4 font-semibold">Merchant</th>
                <th className="p-4 font-semibold">Exchange</th>
                <th className="p-4 font-semibold">Reason</th>
                <th className="p-4 font-semibold">Source</th>
                <th className="p-4 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {filteredList.length > 0 ? filteredList.map((entry) => (
                <tr key={`${entry.exchange}-${entry.merchantId}`} className="hover:bg-slate-800/20 transition-colors">
                  <td className="p-4">
                    <div className="font-bold text-white">{entry.merchantName}</div>
                    <div className="text-xs font-mono text-slate-400 tabular-nums">{entry.merchantId}</div>
                  </td>
                  <td className="p-4">
                    <span className="px-2 py-1 bg-slate-800 text-slate-300 text-xs font-bold rounded uppercase tracking-wider">
                      {entry.exchange}
                    </span>
                  </td>
                  <td className="p-4">
                    <div className="text-sm text-red-400 max-w-xs truncate" title={entry.reason}>
                      {entry.reason}
                    </div>
                  </td>
                  <td className="p-4">
                    <div className="text-xs text-slate-400">{entry.source}</div>
                    <div className="text-xs text-slate-500 font-mono tabular-nums">
                      {new Date(entry.addedAt).toLocaleDateString()}
                    </div>
                  </td>
                  <td className="p-4 text-right">
                    <motion.button
                      whileHover={{ scale: 1.1 }}
                      whileTap={{ scale: 0.9 }}
                      onClick={() => handleUnban(entry.merchantId, entry.exchange)}
                      className="p-2 hover:bg-red-500/10 text-slate-500 hover:text-red-400 rounded-lg transition-colors focus:ring-2 focus:ring-red-500/50 outline-none"
                      title="Unban Merchant"
                    >
                      <Trash2 className="w-4 h-4" />
                    </motion.button>
                  </td>
                </tr>
              )) : (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-slate-400 text-sm">
                    No blacklisted merchants found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
