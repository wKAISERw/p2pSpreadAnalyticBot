import React from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import {
  Cookie, Activity, Brain, MessageSquare, Wifi, WifiOff, Loader2,
  Power, PowerOff, BellOff, Bell, TimerReset, CircleSlash, Stethoscope,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { useExchanges } from '../hooks/useExchanges';
import {
  ExchangeHealthResult, MonitoringOrder, QueueStatus, ScannerState, SessionStatus,
} from '../types';

/**
 * Меню «Моніторинг» + «Система» з бота: стан ядра, пауза алертів, свіжість
 * сесій бірж, незавершені угоди та черги фонових воркерів.
 */
export default function MonitoringPanel() {
  // Особа береться з підтвердженої сесії — раніше тут був ID,
  // введений руками в налаштуваннях, тобто будь-який.
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const isAdmin = useAppStore(state => state.auth?.isAdmin ?? false);

  const { data: scanner, mutate: mutateScanner } = useSWR<ScannerState>(
    '/scanner/state', () => api.getScannerState(),
    { refreshInterval: 5000, shouldRetryOnError: false }
  );
  const { data: queues } = useSWR<QueueStatus>(
    '/monitoring/queues', () => api.getQueues(),
    { refreshInterval: 5000, shouldRetryOnError: false }
  );
  const { data: sessions } = useSWR<SessionStatus[]>(
    telegramId ? ['/monitoring/sessions', telegramId] : null, () => api.getSessions(),
    { refreshInterval: 30000, shouldRetryOnError: false }
  );
  const { data: orders } = useSWR<MonitoringOrder[]>(
    '/monitoring/orders', () => api.getMonitoringOrders(),
    { refreshInterval: 10000, shouldRetryOnError: false }
  );

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      await mutateScanner();
      toast.success(ok);
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Моніторинг</h1>
        <p className="text-sm text-slate-400">Стан ядра, сесій, угод і фонових черг</p>
      </div>

      {/* Керування ядром */}
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
      >
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={cn(
              'p-3 rounded-2xl',
              scanner?.isScannerActive ? 'bg-accent-500/10' : 'bg-slate-800'
            )}>
              {scanner?.isScannerActive
                ? <Power className="w-6 h-6 text-accent-400" />
                : <PowerOff className="w-6 h-6 text-slate-500" />}
            </div>
            <div>
              <div className="font-bold text-white">
                Ядро {scanner?.isScannerActive ? 'працює' : 'зупинено'}
              </div>
              <div className="text-xs text-slate-400">
                {scanner?.isMuted
                  ? `Алерти на паузі ще ${Math.ceil((scanner.muteSecondsLeft ?? 0) / 60)} хв`
                  : 'Алерти увімкнені'}
              </div>
            </div>
          </div>

          {isAdmin ? (
            <div className="flex flex-wrap items-center gap-2">
              {scanner?.isScannerActive ? (
                <ActionButton tone="danger" onClick={() => act(api.stopScanner, 'Ядро зупинено')}>
                  Зупинити ядро
                </ActionButton>
              ) : (
                <ActionButton tone="ok" onClick={() => act(api.startScanner, 'Ядро запущено')}>
                  Запустити ядро
                </ActionButton>
              )}

              {scanner?.isMuted ? (
                <ActionButton tone="neutral" onClick={() => act(() => api.setMute(0), 'Алерти увімкнено')}>
                  <Bell className="w-3.5 h-3.5" /> Зняти паузу
                </ActionButton>
              ) : (
                <>
                  <ActionButton tone="neutral" onClick={() => act(() => api.setMute(1), 'Пауза 1 год')}>
                    <BellOff className="w-3.5 h-3.5" /> 1 год
                  </ActionButton>
                  <ActionButton tone="neutral" onClick={() => act(() => api.setMute(4), 'Пауза 4 год')}>
                    <BellOff className="w-3.5 h-3.5" /> 4 год
                  </ActionButton>
                </>
              )}
            </div>
          ) : (
            <span className="text-xs text-slate-500">Керування доступне в режимі адміністратора</span>
          )}
        </div>
      </motion.section>

      {/* Черги */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Metric icon={<Brain className="w-4 h-4 text-purple-400" />} label="Черга LLM" value={queues?.llmQueue ?? 0} />
        <Metric icon={<MessageSquare className="w-4 h-4 text-blue-400" />} label="Черга відгуків" value={queues?.reviewQueue ?? 0} />
        <Metric icon={<Activity className="w-4 h-4 text-accent-400" />} label="Активні угоди" value={orders?.length ?? 0} />
        <Metric
          icon={queues?.internetConnected
            ? <Wifi className="w-4 h-4 text-accent-400" />
            : <WifiOff className="w-4 h-4 text-red-400" />}
          label="Мережа"
          value={queues?.internetConnected === false ? 'Немає' : 'OK'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ExchangeControl isAdmin={isAdmin} />
        <SessionsCard sessions={sessions} />
      </div>

      <OrdersCard orders={orders} />
      <BreakersCard cbStatus={queues?.cbStatus} />
    </div>
  );
}

/**
 * Варіанти паузи при вимкненні біржі — ті самі, що пропонує меню
 * `exch:cooldown_pick` у боті. 0 означає «до ручного ввімкнення».
 */
const COOLDOWN_CHOICES: { hours: number; label: string }[] = [
  { hours: 1, label: '1 год' },
  { hours: 4, label: '4 год' },
  { hours: 12, label: '12 год' },
  { hours: 24, label: '1 доба' },
  { hours: 0, label: 'Назавжди' },
];

function ExchangeControl({ isAdmin }: { isAdmin: boolean }) {
  const { exchanges } = useExchanges();
  const { mutate } = useSWR('/exchanges');
  // Яку біржу зараз вимикаємо — для неї показуємо вибір паузи.
  const [picking, setPicking] = React.useState<string | null>(null);
  const [health, setHealth] = React.useState<ExchangeHealthResult[] | null>(null);
  const [checking, setChecking] = React.useState(false);

  /**
   * «Health check» із меню моніторингу бота.
   *
   * Лічильник відмов поруч показує минуле — скільки разів біржа вже не
   * відповіла в бойових циклах. Ця кнопка питає її просто зараз, тому й
   * повільна: ExchangeManager робить три спроби на кожну.
   */
  const runHealthCheck = async () => {
    setChecking(true);
    try {
      const result = await api.checkExchangesHealth();
      setHealth(result);
      const dead = result.filter(r => !r.ok);
      if (dead.length) {
        toast.warning(`Не відповідають: ${dead.map(r => r.exchange).join(', ')}`);
      } else {
        toast.success('Усі біржі відповідають');
      }
    } catch (e: any) {
      toast.error(`Перевірка не вдалась: ${e?.message ?? 'помилка'}`);
    } finally {
      setChecking(false);
    }
  };

  const enable = async (name: string) => {
    try {
      await api.enableExchange(name);
      await mutate();
      toast.success(`${name}: увімкнено`);
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    }
  };

  const disable = async (name: string, cooldownHours: number) => {
    setPicking(null);
    try {
      await api.disableExchange(name, cooldownHours);
      await mutate();
      toast.success(
        cooldownHours
          ? `${name}: пауза ${cooldownHours} год`
          : `${name}: вимкнено до ручного ввімкнення`
      );
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    }
  };

  return (
    <Panel
      title="Біржі"
      icon={<CircleSlash className="w-5 h-5 text-orange-400" />}
      action={isAdmin && (
        <button
          onClick={runHealthCheck}
          disabled={checking}
          title="Опитати всі біржі просто зараз (три спроби на кожну)"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold bg-slate-800 border border-slate-700 text-slate-300 hover:text-white disabled:opacity-40 transition-colors"
        >
          {checking
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Stethoscope className="w-3.5 h-3.5" />}
          {checking ? 'Опитую…' : 'Перевірити'}
        </button>
      )}
    >
      {exchanges.length === 0 ? (
        <Muted>Немає даних.</Muted>
      ) : (
        <div className="space-y-2">
          {exchanges.map(ex => (
            <div
              key={ex.name}
              className="px-4 py-2.5 bg-slate-950/50 border border-slate-800/50 rounded-xl"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className={cn(
                    'w-2 h-2 rounded-full shrink-0',
                    ex.enabled ? 'bg-accent-500' : ex.isCooldown ? 'bg-orange-500' : 'bg-slate-600'
                  )} />
                  <span className="text-sm font-bold text-white">{ex.name}</span>
                  {ex.isCooldown && (
                    <span className="flex items-center gap-1 text-[11px] text-orange-400">
                      <TimerReset className="w-3 h-3" />{ex.cooldownRemainingH}г
                    </span>
                  )}
                  {ex.disabledReason && (
                    <span className="text-[11px] text-slate-500 truncate">{ex.disabledReason}</span>
                  )}
                  {(() => {
                    const probe = health?.find(h => h.exchange === ex.name);
                    if (!probe) return null;
                    return (
                      <span
                        title={probe.message}
                        className={cn(
                          'text-[11px] font-bold shrink-0',
                          probe.ok ? 'text-accent-400' : 'text-red-400'
                        )}
                      >
                        {probe.ok ? 'відповідає' : 'мовчить'}
                      </span>
                    );
                  })()}
                </div>

                {isAdmin && (
                  <button
                    onClick={() =>
                      ex.enabled
                        ? setPicking(picking === ex.name ? null : ex.name)
                        : enable(ex.name)
                    }
                    className={cn(
                      'px-3 py-1 rounded-lg text-[10px] font-black uppercase tracking-wider border transition-colors shrink-0',
                      ex.enabled
                        ? 'bg-slate-900 border-slate-700 text-slate-400 hover:text-red-400 hover:border-red-500/30'
                        : 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                    )}
                  >
                    {ex.enabled ? (picking === ex.name ? 'Скасувати' : 'Вимкнути') : 'Увімкнути'}
                  </button>
                )}
              </div>

              {/* Раніше кнопка завжди слала cooldownHours=0, хоча ендпоінт
                  приймав будь-яке значення — тимчасово прибрати біржу з
                  опитування можна було тільки в боті. */}
              {picking === ex.name && (
                <div className="flex flex-wrap items-center gap-1.5 mt-3 pt-3 border-t border-slate-800/50">
                  <span className="text-[11px] text-slate-500 mr-1">Пауза на:</span>
                  {COOLDOWN_CHOICES.map(choice => (
                    <button
                      key={choice.hours}
                      onClick={() => disable(ex.name, choice.hours)}
                      className="px-2.5 py-1 rounded-lg text-[11px] font-bold bg-slate-900 border border-slate-700 text-slate-300 hover:border-red-500/30 hover:text-red-400 transition-colors"
                    >
                      {choice.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SessionsCard({ sessions }: { sessions?: SessionStatus[] }) {
  return (
    <Panel title="Сесії бірж" icon={<Cookie className="w-5 h-5 text-yellow-400" />}>
      <p className="text-xs text-slate-500 -mt-2 mb-3">
        Протухла сесія — найчастіша причина, чому біржа раптом перестає віддавати дані.
      </p>
      {!sessions?.length ? (
        <Muted>Немає даних.</Muted>
      ) : (
        <div className="space-y-2">
          {sessions.map(s => (
            <div
              key={s.exchange}
              className="flex items-center justify-between px-4 py-2.5 bg-slate-950/50 border border-slate-800/50 rounded-xl"
            >
              <span className="text-sm font-bold text-white">{s.exchange}</span>
              {!s.hasSession ? (
                <span className="text-[11px] text-slate-500">немає сесії</span>
              ) : (
                <span className={cn(
                  'text-[11px] font-bold tabular-nums',
                  s.isStale ? 'text-orange-400' : 'text-accent-400'
                )}>
                  {s.isStale ? 'протухла · ' : 'свіжа · '}{s.ageHours}г
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function OrdersCard({ orders }: { orders?: MonitoringOrder[] }) {
  return (
    <Panel title="Незавершені угоди" icon={<Activity className="w-5 h-5 text-accent-400" />}>
      {!orders?.length ? (
        <Muted>Активних угод немає.</Muted>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
                <th className="text-left font-medium py-2 pr-4">Біржа</th>
                <th className="text-left font-medium py-2 pr-4">Ордер</th>
                <th className="text-left font-medium py-2 pr-4">Нога</th>
                <th className="text-right font-medium py-2 pr-4">Сума</th>
                <th className="text-left font-medium py-2">Статус</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {orders.map(o => (
                <tr key={o.id}>
                  <td className="py-2.5 pr-4 font-bold text-white">{o.exchange}</td>
                  <td className="py-2.5 pr-4 font-mono text-xs text-slate-400">{o.orderId}</td>
                  <td className="py-2.5 pr-4 text-slate-400">{o.leg ?? '—'}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums text-slate-200">
                    {o.fiatAmount ? `${Math.round(o.fiatAmount).toLocaleString('uk-UA')} ₴` : '—'}
                  </td>
                  <td className="py-2.5">
                    <span className="px-2 py-0.5 rounded-md bg-blue-500/10 text-blue-400 text-[11px] font-bold">
                      {o.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function BreakersCard({ cbStatus }: { cbStatus?: Record<string, string> }) {
  const entries = Object.entries(cbStatus ?? {});
  if (!entries.length) return null;

  return (
    <Panel title="Circuit breakers" icon={<Activity className="w-5 h-5 text-slate-400" />}>
      <div className="flex flex-wrap gap-2">
        {entries.map(([name, status]) => (
          <div
            key={name}
            className={cn(
              'px-3 py-1.5 rounded-xl border text-xs font-bold',
              status === 'CLOSED'
                ? 'bg-accent-500/10 border-accent-500/20 text-accent-400'
                : status === 'HALF_OPEN'
                  ? 'bg-orange-500/10 border-orange-500/20 text-orange-400'
                  : 'bg-red-500/10 border-red-500/20 text-red-400'
            )}
            title={status === 'CLOSED' ? 'Норма — запити проходять' : 'Запити блокуються'}
          >
            {name} · {status}
          </div>
        ))}
      </div>
    </Panel>
  );
}

// ─── Дрібниці ─────────────────────────────────────────────────────────────

function Panel({
  title, icon, children, action,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  /** Кнопка праворуч від заголовка — напр. ручна перевірка бірж. */
  action?: React.ReactNode;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="p-2 bg-slate-800/60 rounded-xl">{icon}</div>
        <h2 className="text-lg font-bold text-white">{title}</h2>
        {action && <div className="ml-auto">{action}</div>}
      </div>
      {children}
    </motion.section>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: number | string }) {
  return (
    <div className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-4">
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-[11px] uppercase tracking-wider text-slate-400">{label}</span>
      </div>
      <div className="text-2xl font-bold text-white tabular-nums">{value}</div>
    </div>
  );
}

function ActionButton({
  children, onClick, tone,
}: {
  children: React.ReactNode;
  onClick: () => void;
  tone: 'ok' | 'danger' | 'neutral';
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold border transition-colors',
        tone === 'ok' && 'bg-accent-500/10 border-accent-500/30 text-accent-400 hover:bg-accent-500/20',
        tone === 'danger' && 'bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20',
        tone === 'neutral' && 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
      )}
    >
      {children}
    </button>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-slate-500">{children}</p>;
}
