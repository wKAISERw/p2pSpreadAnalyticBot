import React, { useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { FlaskConical, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { FeatureGroup } from '../../types';

/**
 * Експериментальні фічі — каталог із `EXPERIMENTAL_FEATURES` у боті разом
 * зі станом кожної для цього користувача. Опис береться звідти ж, щоб
 * формулювання не розходились із тим, що людина читала в Telegram.
 */
export default function FeaturesSection() {
  const { data: groups, error, isLoading, mutate } = useSWR<FeatureGroup[]>(
    '/user/features',
    () => api.getFeatures(),
    { shouldRetryOnError: false }
  );
  const [busy, setBusy] = useState<string | null>(null);

  if (isLoading) {
    return (
      <Shell>
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Читаю каталог фіч…
        </p>
      </Shell>
    );
  }
  if (error || !groups?.length) {
    return (
      <Shell>
        <p className="text-sm text-slate-500">
          {(error as Error)?.message ?? 'Фіч не знайдено.'}
        </p>
      </Shell>
    );
  }

  const toggle = async (key: string) => {
    setBusy(key);
    try {
      const result = await api.toggleFeature(key);
      toast.success(`${key}: ${result.enabled ? 'увімкнено' : 'вимкнено'}`);
      await mutate();
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Shell>
      <div className="flex items-center gap-3 mb-5">
        <div className="p-2 bg-slate-800/60 rounded-xl">
          <FlaskConical className="w-5 h-5 text-purple-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-white">Експериментальні функції</h2>
          <p className="text-xs text-slate-400">Впливають на поведінку сканера для твого акаунта</p>
        </div>
      </div>

      <div className="space-y-6">
        {groups.map(group => (
          <div key={group.key}>
            <div className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">
              {group.title}
            </div>

            <div className="space-y-2">
              {group.features.map(feature => (
                <button
                  key={feature.key}
                  onClick={() => toggle(feature.key)}
                  disabled={busy === feature.key}
                  className={cn(
                    'w-full flex items-start gap-3 p-4 rounded-2xl border text-left transition-all disabled:opacity-60',
                    feature.enabled
                      ? 'bg-purple-500/5 border-purple-500/25'
                      : 'bg-slate-950/50 border-slate-800 hover:border-slate-700'
                  )}
                >
                  <div className={cn(
                    'w-9 h-5 rounded-full relative transition-colors shrink-0 mt-0.5',
                    feature.enabled ? 'bg-purple-500' : 'bg-slate-700'
                  )}>
                    <div className={cn(
                      'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all',
                      feature.enabled ? 'right-0.5' : 'left-0.5'
                    )} />
                  </div>

                  <div className="min-w-0">
                    <div className={cn(
                      'text-sm font-bold mb-0.5',
                      feature.enabled ? 'text-purple-300' : 'text-slate-300'
                    )}>
                      {feature.name}
                    </div>
                    <div className="text-[11px] text-slate-500 leading-snug">
                      {feature.description}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      {children}
    </motion.section>
  );
}
