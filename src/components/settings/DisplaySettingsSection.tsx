import React, { useState } from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { MessageSquare, Clock, Plus, Trash2, Loader2, Info } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { AutoCooldownTier, DisplaySettings, FilterMode } from '../../types';

/**
 * Налаштування виводу алертів — дзеркало меню «Вивід» у боті.
 *
 * Бекенд зливає частковий payload із поточними значеннями перед записом:
 * репозиторій оновлює всі колонки одним UPDATE, тож без merge вимкнення
 * одного прапорця скидало б решту у дефолти.
 */

const FLAGS: { key: keyof DisplaySettings; label: string; hint: string }[] = [
  { key: 'showAiTermsSummary', label: 'Підсумок умов від AI', hint: 'Коротка вижимка умов мерчанта' },
  { key: 'showFullTerms', label: 'Повний текст умов', hint: 'Оригінальний текст оголошення' },
  { key: 'showAiLogic', label: 'Логіка рішення AI', hint: 'Чому виставлено саме такий вердикт' },
  { key: 'showBankDetails', label: 'Реквізити банку', hint: 'Які банки приймає мерчант' },
  { key: 'showLlmSummary', label: 'Висновок LLM', hint: 'Текстовий підсумок аналізу відгуків' },
  { key: 'groupActiveAlerts', label: 'Групувати активні алерти', hint: 'Складати кілька зв\'язок в одне повідомлення' },
  { key: 'groupScannerAlerts', label: 'Групувати алерти сканера', hint: 'Те саме для потоку зі сканера' },
  { key: 'isHybridRoutesEnabled', label: 'Гібридні маршрути', hint: 'Показувати зв\'язки між різними біржами' },
];

const FILTER_MODES: { value: FilterMode; label: string }[] = [
  { value: 'hide', label: 'Ховати' },
  { value: 'show', label: 'Показувати' },
  { value: 'only', label: 'Тільки такі' },
];

export default function DisplaySettingsSection() {
  const { data, error, isLoading, mutate } = useSWR<DisplaySettings>(
    '/user/display',
    () => api.getDisplaySettings(),
    { shouldRetryOnError: false }
  );

  const [saving, setSaving] = useState<string | null>(null);

  if (isLoading) {
    return (
      <Section>
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="w-4 h-4 animate-spin" /> Читаю налаштування виводу…
        </div>
      </Section>
    );
  }
  if (error || !data) {
    return (
      <Section>
        <p className="text-sm text-slate-400">
          Не вдалось завантажити: {(error as Error)?.message ?? 'немає даних'}
        </p>
      </Section>
    );
  }

  const patch = async (key: string, value: unknown) => {
    setSaving(key);
    // Оптимістично малюємо новий стан, але без revalidate: інакше на
    // повільній мережі перемикач встигне смикнутись назад.
    mutate({ ...data, [key]: value } as DisplaySettings, false);
    try {
      await api.updateDisplaySettings({ [key]: value } as any);
      await mutate();
    } catch (e: any) {
      await mutate();
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(null);
    }
  };

  return (
    <>
      <Section>
        <Header
          icon={<MessageSquare className="w-5 h-5 text-blue-400" />}
          title="Вивід повідомлень"
          subtitle="Що показувати в алертах бота"
        />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {FLAGS.map(flag => (
            <Toggle
              key={flag.key}
              label={flag.label}
              hint={flag.hint}
              checked={Boolean(data[flag.key])}
              busy={saving === flag.key}
              onChange={v => patch(flag.key as string, v)}
            />
          ))}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
          <Field label="ФОП / ТОВ" sub="filter_fop_tov">
            <ModePicker
              value={data.filterFopTov}
              onChange={v => patch('filterFopTov', v)}
            />
          </Field>
          <Field label="Банки / Jar" sub="filter_banka_jar">
            <ModePicker
              value={data.filterBankaJar}
              onChange={v => patch('filterBankaJar', v)}
            />
          </Field>
        </div>

        <div className="mt-6">
          <Field label="Профіль CryptoBot" sub="cryptobot_profile_mode">
            <div className="flex gap-2">
              {(['chat', 'profile'] as const).map(mode => (
                <Chip
                  key={mode}
                  label={mode === 'chat' ? 'Через чат' : 'Через профіль'}
                  active={data.cryptobotProfileMode === mode}
                  onClick={() => patch('cryptobotProfileMode', mode)}
                />
              ))}
            </div>
          </Field>
        </div>
      </Section>

      <CooldownSection data={data} onSaved={mutate} patch={patch} />
    </>
  );
}

function CooldownSection({
  data, onSaved, patch,
}: {
  data: DisplaySettings;
  onSaved: () => void;
  patch: (key: string, value: unknown) => Promise<void>;
}) {
  const auto = data.autoCooldownJson;
  const [tiers, setTiers] = useState<AutoCooldownTier[]>(auto?.tiers ?? []);
  const [windowSeconds, setWindowSeconds] = useState(auto?.windowSeconds ?? 5);
  const usesAuto = data.alertCooldown < 0;

  const saveTiers = async () => {
    try {
      // Сканер іде списком зверху вниз і бере перший рівень, поріг якого
      // ще не перевищено — бекенд сортує, тут лише не даємо зберегти пусте.
      await api.updateAutoCooldown(windowSeconds, tiers);
      toast.success('Драбинку збережено');
      onSaved();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    }
  };

  return (
    <Section>
      <Header
        icon={<Clock className="w-5 h-5 text-orange-400" />}
        title="Пауза між алертами"
        subtitle="Щоб при напливі спредів не залити чат"
      />

      <div className="flex flex-wrap gap-2 mb-5">
        <Chip label="Авто" active={usesAuto} onClick={() => patch('alertCooldown', -1)} />
        {[0, 1, 3, 5, 10].map(sec => (
          <Chip
            key={sec}
            label={sec === 0 ? 'Без паузи' : `${sec} с`}
            active={!usesAuto && data.alertCooldown === sec}
            onClick={() => patch('alertCooldown', sec)}
          />
        ))}
      </div>

      {usesAuto && (
        <div className="bg-slate-950/50 border border-slate-800/50 rounded-2xl p-5">
          <div className="flex items-start gap-1.5 text-[11px] text-slate-500 mb-4">
            <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              Чим більше алертів у вікні, тим більша пауза між ними.
              Береться перший рівень, поріг якого ще не перевищено.
            </span>
          </div>

          <Field label="Вікно спостереження (с)" sub="window_seconds">
            <input
              type="number"
              step={0.5}
              value={windowSeconds}
              onChange={e => setWindowSeconds(parseFloat(e.target.value) || 0)}
              className="w-32 bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm font-bold text-white focus:border-accent-500 outline-none tabular-nums"
            />
          </Field>

          <div className="space-y-2 mt-4">
            {tiers.map((tier, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="text-xs text-slate-500 w-20 shrink-0">до</span>
                <input
                  type="number"
                  value={tier.threshold}
                  onChange={e => {
                    const next = [...tiers];
                    next[i] = { ...tier, threshold: parseInt(e.target.value, 10) || 0 };
                    setTiers(next);
                  }}
                  className="w-24 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white outline-none tabular-nums"
                />
                <span className="text-xs text-slate-500 shrink-0">алертів → пауза</span>
                <input
                  type="number"
                  step={0.1}
                  value={tier.delay}
                  onChange={e => {
                    const next = [...tiers];
                    next[i] = { ...tier, delay: parseFloat(e.target.value) || 0 };
                    setTiers(next);
                  }}
                  className="w-24 bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-sm text-white outline-none tabular-nums"
                />
                <span className="text-xs text-slate-500 shrink-0">с</span>
                <button
                  onClick={() => setTiers(tiers.filter((_, j) => j !== i))}
                  className="p-1.5 text-slate-500 hover:text-red-400 transition-colors"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>

          <div className="flex gap-2 mt-4">
            <button
              onClick={() => setTiers([...tiers, { threshold: 10, delay: 0.5 }])}
              className="flex items-center gap-1.5 px-4 py-2 bg-slate-800 hover:bg-slate-700 text-white text-xs font-bold rounded-xl transition-colors"
            >
              <Plus className="w-3.5 h-3.5" /> Рівень
            </button>
            <button
              onClick={saveTiers}
              disabled={tiers.length === 0}
              className="px-5 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-colors"
            >
              Зберегти драбинку
            </button>
          </div>
        </div>
      )}
    </Section>
  );
}

// ─── Дрібниці ─────────────────────────────────────────────────────────────

function Section({ children }: { children: React.ReactNode }) {
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

function Header({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle: string }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div className="p-2 bg-slate-800/60 rounded-xl">{icon}</div>
      <div>
        <h2 className="text-lg font-bold text-white">{title}</h2>
        <p className="text-xs text-slate-400">{subtitle}</p>
      </div>
    </div>
  );
}

function Field({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm font-bold text-white mb-0.5">{label}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono mb-2">{sub}</div>}
      {children}
    </div>
  );
}

const Chip: React.FC<{ label: string; active: boolean; onClick: () => void }> = ({
  label, active, onClick,
}) => (
  <button
    onClick={onClick}
    className={cn(
      'px-4 py-2 rounded-xl text-xs font-bold border transition-all',
      active
        ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
        : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
    )}
  >
    {label}
  </button>
);

function ModePicker({ value, onChange }: { value: FilterMode; onChange: (v: FilterMode) => void }) {
  return (
    <div className="flex gap-2">
      {FILTER_MODES.map(m => (
        <Chip key={m.value} label={m.label} active={value === m.value} onClick={() => onChange(m.value)} />
      ))}
    </div>
  );
}

const Toggle: React.FC<{
  label: string; hint: string; checked: boolean; busy?: boolean; onChange: (v: boolean) => void;
}> = ({ label, hint, checked, busy, onChange }) => (
  <button
    onClick={() => onChange(!checked)}
    disabled={busy}
    className={cn(
      'flex items-start gap-3 p-4 rounded-2xl border text-left transition-all disabled:opacity-60',
      checked ? 'bg-accent-500/5 border-accent-500/25' : 'bg-slate-950/50 border-slate-800'
    )}
  >
    <div className={cn(
      'w-9 h-5 rounded-full relative transition-colors shrink-0 mt-0.5',
      checked ? 'bg-accent-500' : 'bg-slate-700'
    )}>
      <div className={cn(
        'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all',
        checked ? 'right-0.5' : 'left-0.5'
      )} />
    </div>
    <div className="min-w-0">
      <div className={cn('text-sm font-bold', checked ? 'text-accent-400' : 'text-slate-300')}>
        {label}
      </div>
      <div className="text-[11px] text-slate-500 leading-snug">{hint}</div>
    </div>
  </button>
);
