import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { Landmark, Loader2, Info, Moon, CalendarDays, Link2, Percent } from 'lucide-react';
import { toast } from 'sonner';
import { cn, toCamel } from '../../lib/utils';
import { api } from '../../services/api';
import { BankLimits, BankProfile, LimitField, UNLIMITED_LIMIT } from '../../types';
import { useBankProfiles, licensePartners } from '../../hooks/useBankProfiles';

/**
 * Ліміти по банках. Пріоритет: override картки → ліміт банку → довідник.
 *
 * Раніше поля показували нулі, поки користувач нічого не задав, — тобто
 * реальні числа, за якими працює движок, на сайті були невидимі. Тепер
 * бекенд віддає ефективні значення, а тут видно, які з них узяті з
 * довідника, а які людина виставила руками.
 */

const FIELDS: { key: LimitField; label: string; step: number }[] = [
  { key: 'daily_out_max', label: 'Витрати за добу (₴)', step: 1000 },
  { key: 'daily_in_max', label: 'Надходження за добу (₴)', step: 1000 },
  { key: 'monthly_out_max', label: 'Витрати за місяць (₴)', step: 5000 },
  { key: 'monthly_in_max', label: 'Надходження за місяць (₴)', step: 5000 },
  { key: 'max_single_tx_out', label: 'Макс. одна витрата (₴)', step: 500 },
  { key: 'max_single_tx_in', label: 'Макс. одне надходження (₴)', step: 500 },
  { key: 'max_tx_per_day', label: 'Транзакцій за добу', step: 1 },
  { key: 'cooldown_hours', label: 'Пауза після ліміту (год)', step: 1 },
];

/** snake_case поля з бекенда приходять камелізованими — див. lib/utils. */
const camel = (key: string) => toCamel<string>(key) as keyof BankLimits;

export default function BankLimitsSection() {
  const { data: limits = [], mutate, isLoading } = useSWR<BankLimits[]>(
    '/user/bank-limits',
    () => api.getBankLimits(),
    { shouldRetryOnError: false }
  );
  const { profiles } = useBankProfiles();

  const [bank, setBank] = useState('');
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [saving, setSaving] = useState(false);

  // Ключ банку — канонічний слаг (monobank, a-bank), той самий, що в БД.
  // Брати його з людської назви не можна: `'ПУМБ'.toLowerCase()` дає
  // «пумб», якого движок не знає, і ліміти лягали б у неіснуючий банк.
  const knownBanks = Array.from(
    new Set([...profiles.map(p => p.slug), ...limits.map(l => l.bankName)])
  ).sort();

  useEffect(() => {
    if (!bank && knownBanks.length) setBank(knownBanks[0]);
  }, [knownBanks, bank]);

  useEffect(() => { setDraft({}); }, [bank]);

  const stored = limits.find(l => l.bankName === bank);
  const profile = profiles.find(p => p.slug === bank);
  const userSet = new Set(stored?.userSet ?? []);

  // Бекенд уже злив довідник із тим, що задав користувач, тож тут завжди
  // те саме число, за яким піде движок.
  const effective = (key: LimitField): number => {
    if (draft[key] !== undefined) return draft[key];
    const fromApi = stored?.[camel(key)];
    if (typeof fromApi === 'number') return fromApi;
    const fromProfile = profile?.defaultLimits?.[key];
    return typeof fromProfile === 'number' ? fromProfile : 0;
  };

  const hasChanges = Object.keys(draft).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      await api.setBankLimits(bank, draft);
      toast.success(`Ліміти ${profile?.name ?? bank} збережено`);
      setDraft({});
      await mutate();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <Landmark className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Ліміти банків</h2>
            <p className="text-xs text-slate-400">Застосовуються до всіх карток цього банку</p>
          </div>
        </div>

        <button
          onClick={save}
          disabled={!hasChanges || saving || !bank}
          className="flex items-center gap-2 px-5 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-all"
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Зберегти
        </button>
      </div>

      {isLoading ? (
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Завантажую…
        </p>
      ) : !knownBanks.length ? (
        <p className="text-sm text-slate-500">Немає банків — спершу додай картку в боті.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-5">
            {knownBanks.map(slug => {
              const p = profiles.find(x => x.slug === slug);
              return (
                <button
                  key={slug}
                  onClick={() => setBank(slug)}
                  title={p ? `Tier ${p.tier}` : undefined}
                  className={cn(
                    'px-4 py-2 rounded-xl text-xs font-bold border transition-all',
                    bank === slug
                      ? 'bg-blue-500/10 border-blue-500/30 text-blue-300'
                      : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
                  )}
                >
                  {p?.name ?? slug}
                  {limits.some(l => l.bankName === slug && (l.userSet?.length ?? 0) > 0) && (
                    <span className="ml-1.5 text-blue-400" title="Є власні налаштування">●</span>
                  )}
                </button>
              );
            })}
          </div>

          {profile && <BankProfileCard profile={profile} profiles={profiles} />}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {FIELDS.map(field => {
              const isUserSet = userSet.has(field.key) || draft[field.key] !== undefined;
              const val = effective(field.key);
              return (
                <div key={field.key}>
                  <div className="text-xs font-bold text-white mb-0.5">{field.label}</div>
                  <div className="flex items-center gap-1.5 mb-2">
                    <span className="text-[10px] text-slate-500 font-mono">{field.key}</span>
                    <span
                      className={cn(
                        'text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded',
                        isUserSet
                          ? 'bg-blue-500/15 text-blue-300'
                          : 'bg-slate-800 text-slate-500'
                      )}
                    >
                      {isUserSet ? 'вручну' : 'довідник'}
                    </span>
                  </div>
                  <input
                    type="number"
                    step={field.step}
                    value={val}
                    onChange={e =>
                      setDraft(prev => ({ ...prev, [field.key]: parseFloat(e.target.value) || 0 }))
                    }
                    className={cn(
                      'w-full bg-slate-950 border rounded-xl px-3 py-2 text-sm font-bold text-white focus:border-accent-500 outline-none tabular-nums',
                      isUserSet ? 'border-blue-500/25' : 'border-slate-800'
                    )}
                  />
                  {val === UNLIMITED_LIMIT && (
                    <div className="text-[10px] text-slate-500 mt-1">без обмеження</div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="flex items-start gap-1.5 mt-4 text-[11px] text-slate-500 leading-snug">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              <b>−1 = без обмеження.</b> Нуль означає буквально нуль, тобто
              через картку не пройде нічого. Поля з позначкою «довідник»
              беруться з операційного профілю банку — їх можна не чіпати.
              Окрема картка може мати власні ліміти, вони перебивають ці.
            </span>
          </div>
        </>
      )}
    </motion.section>
  );
}

/**
 * Що відомо про банк, крім цифр у полях.
 *
 * Ліміти без цього контексту виглядають як магічні числа: незрозуміло,
 * чому в Izibank стоїть 3 транзакції, а в Monobank 15, і чому переказ
 * коштує 2%, хоч у сусідньому банку нічого.
 */
const BankProfileCard: React.FC<{ profile: BankProfile; profiles: BankProfile[] }> = ({
  profile, profiles,
}) => {
  const partners = licensePartners(profiles, profile.slug);
  const fee = profile.p2pFee;
  const night = profile.nightWindow;

  const facts: { icon: React.ReactNode; text: React.ReactNode }[] = [];

  if (fee && (fee.pct > 0 || fee.fixedUah > 0)) {
    facts.push({
      icon: <Percent className="w-3.5 h-3.5" />,
      text: fee.label || `${fee.pct}%${fee.fixedUah ? ` + ${fee.fixedUah} ₴` : ''}`,
    });
  }

  if (night) {
    facts.push({
      icon: <Moon className="w-3.5 h-3.5" />,
      text: night.maxUah === null
        ? `${night.fromHour}:00–${night.toHour}:00 перекази не проходять`
        : `${night.fromHour}:00–${night.toHour}:00 не більше ${night.maxUah.toLocaleString('uk-UA')} ₴`,
    });
  }

  if (profile.businessDaysOnly) {
    facts.push({
      icon: <CalendarDays className="w-3.5 h-3.5" />,
      text: 'IBAN відправляє лише в робочі дні',
    });
  }

  if (partners.length > 0) {
    facts.push({
      icon: <Link2 className="w-3.5 h-3.5" />,
      text: (
        <>
          Спільна ліцензія й фінмон із{' '}
          <b>{partners.map(p => p.name).join(', ')}</b> — блок на одному
          банку тягне другий
        </>
      ),
    });
  }

  if (profile.note) {
    facts.push({ icon: <Info className="w-3.5 h-3.5" />, text: profile.note });
  }

  return (
    <div className="mb-5 p-4 rounded-2xl bg-slate-950/40 border border-slate-800/60">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
        <span className="text-sm font-bold text-white">{profile.name}</span>
        <span
          className={cn(
            'text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded',
            profile.tier === 1
              ? 'bg-accent-500/15 text-accent-400'
              : profile.tier === 2
                ? 'bg-blue-500/15 text-blue-300'
                : 'bg-slate-800 text-slate-400'
          )}
        >
          tier {profile.tier}
        </span>
        {profile.safeMonthlyUah && (
          <span className="text-[11px] text-slate-500 tabular-nums">
            безпечно до {profile.safeMonthlyUah.toLocaleString('uk-UA')} ₴/міс
          </span>
        )}
        {profile.safeTxPerDay && (
          <span className="text-[11px] text-slate-500 tabular-nums">
            · {profile.safeTxPerDay} переказів/добу
          </span>
        )}
      </div>

      {facts.length > 0 && (
        <div className="space-y-1">
          {facts.map((f, i) => (
            <div key={i} className="flex items-start gap-2 text-[11px] text-slate-400 leading-snug">
              <span className="text-slate-600 shrink-0 mt-px">{f.icon}</span>
              <span>{f.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
