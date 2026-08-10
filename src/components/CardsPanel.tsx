import React, { useState } from 'react';
import useSWR from 'swr';
import { motion, AnimatePresence } from 'motion/react';
import {
  CreditCard, ArrowDownLeft, ArrowUpRight, Loader2, ChevronDown, Link2, Settings2,
  Plus, X, Pencil, Trash2, Snowflake, Check,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../lib/utils';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { Card, CardCategory, CardReportRow, CardTransaction } from '../types';
import { CardSettings } from './cards/CardSettings';
import { RejectionStats } from './cards/RejectionStats';
import { useBankProfiles } from '../hooks/useBankProfiles';
import LoadingVeil from './LoadingVeil';

const uah = (v: number) => `${Math.round(v ?? 0).toLocaleString('uk-UA')} ₴`;

/**
 * Ліміт картки з `get_card_effective_limits`.
 *
 * Ключі камелізовані на беку: `daily_out_max` → `dailyOutMax`. Тут довго
 * шукалось `daily_out` / `dailyOut` / `day_out` — жодного з них не існує,
 * тож усі прогрес-бари мовчки писали «ліміт не заданий», хоча движок
 * лімітами користувався.
 *
 * −1 означає «без обмеження» (config.banks.UNLIMITED) — шкали для такого
 * немає, тому теж null.
 */
const limitOf = (card: Card, ...keys: string[]): number | null => {
  for (const key of keys) {
    const v = card.limits?.[key];
    if (typeof v === 'number' && v > 0) return v;
  }
  return null;
};

/**
 * Картки користувача з /api/v1/cards: баланс, ліміти й вибірка за добу
 * та календарний місяць. Раніше це було доступно тільки в меню «Картки».
 */
export default function CardsPanel() {
  // Особа береться з підтвердженої сесії — раніше тут був ID,
  // введений руками в налаштуваннях, тобто будь-який.
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const [isAdding, setIsAdding] = useState(false);

  const { data: cards, error, isLoading, mutate } = useSWR<Card[]>(
    telegramId ? ['/cards', telegramId] : null,
    () => api.getCards(telegramId!),
    { refreshInterval: 30000, shouldRetryOnError: false }
  );

  if (!telegramId) return <Empty text="Потрібен вхід через Telegram." />;
  if (isLoading) return <LoadingVeil compact label="Читаю картки" />;
  if (error) return <Empty text={`Не вдалось завантажити картки: ${(error as Error).message}`} />;

  const list = cards ?? [];
  const totalBalance = list.reduce((sum, c) => sum + (c.balance ?? 0), 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Картки</h1>
          <p className="text-sm text-slate-400">
            {list.length} шт · загальний баланс {uah(totalBalance)}
          </p>
        </div>

        <button
          onClick={() => setIsAdding(v => !v)}
          className="flex items-center gap-2 px-4 py-2.5 bg-accent-500 hover:bg-accent-400 text-slate-950 text-xs font-bold rounded-xl transition-colors shrink-0"
        >
          {isAdding ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
          {isAdding ? 'Скасувати' : 'Додати картку'}
        </button>
      </div>

      <AnimatePresence>
        {isAdding && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <AddCardForm
              onDone={async () => {
                setIsAdding(false);
                await mutate();
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {list.length === 0 ? (
        <Empty text="Карток ще немає — додай першу кнопкою вище або в боті, меню «Картки»." />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {list.map(card => <CardItem key={card.id} card={card} onChanged={mutate} />)}
        </div>
      )}

      <RejectionStats />

      <CardsReport />
    </div>
  );
}

/** Поки довідник летить — щоб форма не була порожньою. */
const BANK_FALLBACK: { value: string; label: string }[] = [
  { value: 'monobank', label: 'Monobank' },
  { value: 'privatbank', label: 'ПриватБанк' },
  { value: 'pumb', label: 'ПУМБ' },
];

const TIER_TITLES: Record<number, string> = {
  1: 'Найкраще тримають оборот',
  2: 'Робочі',
  3: 'Обережно',
};

/**
 * Вибір банку з довідника, а не з копії списку.
 *
 * Тут довго жили шість жорстко вписаних банків, тож завести через сайт
 * картку Ощадбанку чи Таскомбанку було неможливо — хоч бот їх знає й
 * рахує їхні ліміти. Групування за tier — не прикраса: від нього залежать
 * дефолтні ліміти, які картка отримає одразу після створення.
 */
function BankPicker({
  value, onChange,
}: { value: string; onChange: (slug: string) => void }) {
  const { profiles, isLoading } = useBankProfiles();

  if (isLoading || profiles.length === 0) {
    return (
      <div className="flex flex-wrap gap-2">
        {BANK_FALLBACK.map(bank => (
          <button
            key={bank.value}
            onClick={() => onChange(bank.value)}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
              value === bank.value
                ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
            )}
          >
            {bank.label}
          </button>
        ))}
      </div>
    );
  }

  const tiers = [1, 2, 3].filter(t => profiles.some(p => p.tier === t));
  const picked = profiles.find(p => p.slug === value);

  return (
    <div className="space-y-2">
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/40 outline-none transition-all"
      >
        {tiers.map(tier => (
          <optgroup key={tier} label={TIER_TITLES[tier] ?? `Tier ${tier}`}>
            {profiles
              .filter(p => p.tier === tier)
              .map(p => (
                <option key={p.slug} value={p.slug}>{p.name}</option>
              ))}
          </optgroup>
        ))}
      </select>

      {picked && (
        <p className="text-xs text-slate-500">
          Ліміти за замовчуванням:{' '}
          {picked.safeMonthlyUah
            ? `${uah(picked.safeMonthlyUah)} на місяць`
            : 'загальні'}
          {picked.safeTxPerDay ? ` · до ${picked.safeTxPerDay} переказів на добу` : ''}
          {picked.licenseGroup ? ' · спільна ліцензія з банком-партнером' : ''}
        </p>
      )}
    </div>
  );
}

const CATEGORIES: { value: CardCategory; label: string }[] = [
  { value: 'self', label: '🙋 Власна' },
  { value: 'relative', label: '👪 Родич' },
  { value: 'friend', label: '🤝 Друг' },
  { value: 'drop', label: '💼 Дроп' },
];

/**
 * Заведення картки — те саме, що майстер `card:add_start` у боті.
 *
 * Повний номер необов'язковий: він потрібен лише для звірки з випискою
 * Monobank. Без нього достатньо останніх чотирьох цифр, і картка працює
 * як ручний облік лімітів.
 */
function AddCardForm({ onDone }: { onDone: () => Promise<void> }) {
  const [bankName, setBankName] = useState('monobank');
  const [cardNumber, setCardNumber] = useState('');
  const [lastFour, setLastFour] = useState('');
  const [label, setLabel] = useState('');
  const [category, setCategory] = useState<CardCategory>('self');
  const [balance, setBalance] = useState(0);
  const [saving, setSaving] = useState(false);

  const digits = cardNumber.replace(/\D/g, '');
  const hasFullNumber = digits.length === 16;
  const hasLastFour = /^\d{4}$/.test(lastFour.trim());
  const canSubmit = Boolean(bankName) && (hasFullNumber || (!digits && hasLastFour));

  const submit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    try {
      await api.createCard({
        bankName,
        cardNumber: hasFullNumber ? digits : undefined,
        lastFour: hasFullNumber ? undefined : lastFour.trim(),
        label: label.trim(),
        category,
        balance,
      });
      toast.success('Картку додано');
      await onDone();
    } catch (e: any) {
      toast.error(`Не додано: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6 space-y-5">
      <div>
        <FieldLabel>Банк</FieldLabel>
        <BankPicker value={bankName} onChange={setBankName} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <FieldLabel>Номер картки</FieldLabel>
          <input
            value={cardNumber}
            onChange={e => setCardNumber(e.target.value)}
            inputMode="numeric"
            placeholder="16 цифр"
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white font-mono focus:border-accent-500 outline-none transition-all"
          />
          <p className="text-[11px] text-slate-500 mt-1.5">
            Потрібен, щоб бот звіряв надходження з випискою Monobank.
            {digits.length > 0 && !hasFullNumber && (
              <span className="text-orange-400"> Введено {digits.length} із 16.</span>
            )}
          </p>
        </div>

        <div>
          <FieldLabel>або останні 4 цифри</FieldLabel>
          <input
            value={lastFour}
            onChange={e => setLastFour(e.target.value)}
            inputMode="numeric"
            maxLength={4}
            disabled={digits.length > 0}
            placeholder="1234"
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white font-mono focus:border-accent-500 outline-none transition-all disabled:opacity-40"
          />
          <p className="text-[11px] text-slate-500 mt-1.5">
            Якщо звірка не потрібна — вистачить їх.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <FieldLabel>Мітка</FieldLabel>
          <input
            value={label}
            onChange={e => setLabel(e.target.value)}
            placeholder="напр. «основна» або «Дроп Іван»"
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white focus:border-accent-500 outline-none transition-all"
          />
        </div>
        <div>
          <FieldLabel>Поточний баланс (₴)</FieldLabel>
          <input
            type="number"
            value={balance}
            onChange={e => setBalance(parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm text-white tabular-nums focus:border-accent-500 outline-none transition-all"
          />
        </div>
      </div>

      <div>
        <FieldLabel>Чия картка</FieldLabel>
        <div className="flex flex-wrap gap-2">
          {CATEGORIES.map(cat => (
            <button
              key={cat.value}
              onClick={() => setCategory(cat.value)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
                category === cat.value
                  ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                  : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
              )}
            >
              {cat.label}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={submit}
        disabled={!canSubmit || saving}
        className="flex items-center gap-2 px-6 py-2.5 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-all"
      >
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
        Додати картку
      </button>
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-bold text-slate-400 uppercase tracking-widest mb-2">
      {children}
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  active: 'активна',
  inactive: 'неактивна',
  cooldown: 'пауза',
  frozen: 'заморожена',
  frozen_funds: 'кошти заморожені',
  blocked: 'заблокована',
};

const CardItem: React.FC<{ card: Card; onChanged: () => void }> = ({ card, onChanged }) => {
  const [panel, setPanel] = useState<'none' | 'transactions' | 'settings' | 'edit'>('none');
  const [busy, setBusy] = useState(false);

  const dailyOut = limitOf(card, 'dailyOutMax');
  const monthlyOut = limitOf(card, 'monthlyOutMax');
  const dailyIn = limitOf(card, 'dailyInMax');
  const monthlyIn = limitOf(card, 'monthlyInMax');

  const isFrozen = card.status === 'frozen_funds';

  // Той самий перемикач, що `card:toggle` у боті: активна ↔ кошти заморожені.
  const toggleFrozen = async () => {
    setBusy(true);
    try {
      await api.updateCard(card.id, { status: isFrozen ? 'active' : 'frozen_funds' });
      toast.success(isFrozen ? 'Картку розморожено' : 'Кошти позначено як заморожені');
      onChanged();
    } catch (e: any) {
      toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    // Разом із карткою підуть її транзакції — попереджаємо явно, бо
    // відновити їх нізвідки.
    const name = card.label || `${card.bankName} ••••${card.lastFour}`;
    if (!window.confirm(`Видалити картку «${name}» разом з історією транзакцій?`)) return;

    setBusy(true);
    try {
      await api.deleteCard(card.id);
      toast.success(`Картку «${name}» видалено`);
      onChanged();
    } catch (e: any) {
      toast.error(`Не вдалось видалити: ${e?.message ?? 'помилка'}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        'bg-slate-900/50 border rounded-3xl overflow-hidden',
        isFrozen ? 'border-blue-500/25' : 'border-slate-800/50'
      )}
    >
      <div className="p-6">
        <div className="flex items-start justify-between mb-5 gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2.5 bg-slate-800/60 rounded-xl shrink-0">
              <CreditCard className={cn('w-5 h-5', isFrozen ? 'text-blue-400' : 'text-accent-400')} />
            </div>
            <div className="min-w-0">
              <div className="font-bold text-white truncate">{card.label || 'Без назви'}</div>
              <div className="text-xs text-slate-400 capitalize truncate">
                {card.bankName} · •••• {card.lastFour}
              </div>
            </div>
          </div>

          <div className="text-right shrink-0">
            <div className="text-xl font-black text-white tabular-nums">{uah(card.balance)}</div>
            {card.status && (
              <div className={cn(
                'text-[10px] uppercase tracking-wider',
                card.status === 'active' ? 'text-slate-500' : 'text-blue-400'
              )}>
                {STATUS_LABELS[card.status] ?? card.status}
              </div>
            )}
          </div>
        </div>

        {card.note && (
          <p className="text-[11px] text-slate-500 italic mb-4 line-clamp-2">{card.note}</p>
        )}

        <div className="flex flex-wrap gap-2 mb-5">
          <IconAction
            icon={<Pencil className="w-3.5 h-3.5" />}
            label="Редагувати"
            active={panel === 'edit'}
            onClick={() => setPanel(panel === 'edit' ? 'none' : 'edit')}
          />
          <IconAction
            icon={<Snowflake className="w-3.5 h-3.5" />}
            label={isFrozen ? 'Розморозити' : 'Заморозити'}
            disabled={busy}
            onClick={toggleFrozen}
          />
          <IconAction
            icon={<Trash2 className="w-3.5 h-3.5" />}
            label="Видалити"
            tone="danger"
            disabled={busy}
            onClick={remove}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <UsageBlock
            icon={<ArrowUpRight className="w-3.5 h-3.5" />}
            label="Витрати"
            tone="text-red-400"
            day={card.usedDaily?.out ?? 0}
            month={card.usedMonthly?.out ?? 0}
            dayLimit={dailyOut}
            monthLimit={monthlyOut}
          />
          <UsageBlock
            icon={<ArrowDownLeft className="w-3.5 h-3.5" />}
            label="Надходження"
            tone="text-accent-400"
            day={card.usedDaily?.in ?? 0}
            month={card.usedMonthly?.in ?? 0}
            dayLimit={dailyIn}
            monthLimit={monthlyIn}
          />
        </div>
      </div>

      <div className="flex border-t border-slate-800/50">
        <button
          onClick={() => setPanel(panel === 'transactions' ? 'none' : 'transactions')}
          className="flex-1 px-6 py-3 flex items-center justify-center gap-2 text-xs font-bold text-slate-400 hover:text-white hover:bg-slate-800/30 transition-colors"
        >
          Транзакції
          <ChevronDown className={cn('w-4 h-4 transition-transform', panel === 'transactions' && 'rotate-180')} />
        </button>
        <div className="w-px bg-slate-800/50" />
        <button
          onClick={() => setPanel(panel === 'settings' ? 'none' : 'settings')}
          className="flex-1 px-6 py-3 flex items-center justify-center gap-2 text-xs font-bold text-slate-400 hover:text-white hover:bg-slate-800/30 transition-colors"
        >
          <Settings2 className="w-3.5 h-3.5" />
          Ліміти й сповіщення
        </button>
      </div>

      <AnimatePresence>
        {panel !== 'none' && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden border-t border-slate-800/50"
          >
            {panel === 'transactions' && <Transactions cardId={card.id} />}
            {panel === 'settings' && <CardSettings card={card} onSaved={onChanged} />}
            {panel === 'edit' && (
              <EditCardForm
                card={card}
                onSaved={() => {
                  setPanel('none');
                  onChanged();
                }}
              />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function IconAction({
  icon, label, onClick, active, disabled, tone,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  tone?: 'danger';
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold border transition-colors disabled:opacity-40',
        tone === 'danger'
          ? 'bg-slate-950 border-slate-800 text-slate-400 hover:text-red-400 hover:border-red-500/30'
          : active
            ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
            : 'bg-slate-950 border-slate-800 text-slate-400 hover:text-white hover:border-slate-700'
      )}
    >
      {icon}
      {label}
    </button>
  );
}

/**
 * Мітка, нотатка, категорія і баланс — те саме, що `card:edit:*` у боті.
 *
 * Баланс тут правиться руками навмисно: автоматично його оновлює лише
 * Monobank-вебхук, а для карток інших банків єдине джерело — людина.
 */
function EditCardForm({ card, onSaved }: { card: Card; onSaved: () => void }) {
  const [label, setLabel] = useState(card.label ?? '');
  const [note, setNote] = useState(card.note ?? '');
  const [category, setCategory] = useState<CardCategory>(card.category ?? 'self');
  const [balance, setBalance] = useState(card.balance ?? 0);
  const [saving, setSaving] = useState(false);

  const dirty =
    label !== (card.label ?? '') ||
    note !== (card.note ?? '') ||
    category !== (card.category ?? 'self') ||
    balance !== (card.balance ?? 0);

  const save = async () => {
    if (!dirty) return;
    setSaving(true);
    try {
      await api.updateCard(card.id, {
        label,
        note,
        category,
        ...(balance !== (card.balance ?? 0) ? { balance } : {}),
      });
      toast.success('Картку оновлено');
      onSaved();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-6 py-5 space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <FieldLabel>Мітка</FieldLabel>
          <input
            value={label}
            onChange={e => setLabel(e.target.value)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm text-white focus:border-accent-500 outline-none"
          />
        </div>
        <div>
          <FieldLabel>Баланс (₴)</FieldLabel>
          <input
            type="number"
            value={balance}
            onChange={e => setBalance(parseFloat(e.target.value) || 0)}
            className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm text-white tabular-nums focus:border-accent-500 outline-none"
          />
        </div>
      </div>

      <div>
        <FieldLabel>Нотатка</FieldLabel>
        <input
          value={note}
          onChange={e => setNote(e.target.value)}
          placeholder="що варто памʼятати про цю картку"
          className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2 text-sm text-white focus:border-accent-500 outline-none"
        />
      </div>

      <div>
        <FieldLabel>Чия картка</FieldLabel>
        <div className="flex flex-wrap gap-2">
          {CATEGORIES.map(cat => (
            <button
              key={cat.value}
              onClick={() => setCategory(cat.value)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
                category === cat.value
                  ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                  : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
              )}
            >
              {cat.label}
            </button>
          ))}
        </div>
      </div>

      <button
        onClick={save}
        disabled={!dirty || saving}
        className="flex items-center gap-2 px-5 py-2 bg-accent-500 hover:bg-accent-400 disabled:opacity-40 text-slate-950 text-xs font-bold rounded-xl transition-all"
      >
        {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        Зберегти
      </button>
    </div>
  );
}

function UsageBlock({
  icon, label, tone, day, month, dayLimit, monthLimit,
}: {
  icon: React.ReactNode;
  label: string;
  tone: string;
  day: number;
  month: number;
  dayLimit: number | null;
  monthLimit: number | null;
}) {
  return (
    <div className="bg-slate-950/50 rounded-2xl p-4 border border-slate-800/50">
      <div className={cn('flex items-center gap-1.5 text-xs font-bold mb-3', tone)}>
        {icon}
        {label}
      </div>
      <UsageBar caption="Доба" used={day} limit={dayLimit} />
      <UsageBar caption="Місяць" used={month} limit={monthLimit} />
    </div>
  );
}

function UsageBar({ caption, used, limit }: { caption: string; used: number; limit: number | null }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;

  return (
    <div className="mb-2 last:mb-0">
      <div className="flex justify-between text-[11px] text-slate-400 mb-1 tabular-nums">
        <span>{caption}</span>
        <span>{limit ? `${uah(used)} / ${uah(limit)}` : uah(used)}</span>
      </div>
      {limit ? (
        <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all',
              pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-orange-500' : 'bg-accent-500'
            )}
            style={{ width: `${pct}%` }}
          />
        </div>
      ) : (
        <div className="text-[10px] text-slate-600">ліміт не заданий</div>
      )}
    </div>
  );
}

function Transactions({ cardId }: { cardId: string }) {
  const { data: txs, isLoading } = useSWR<CardTransaction[]>(
    ['/cards/transactions', cardId],
    () => api.getCardTransactions(cardId, 30),
    { shouldRetryOnError: false }
  );

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 px-6 py-6 text-xs text-slate-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> Завантажую…
      </div>
    );
  }
  if (!txs?.length) {
    return <div className="px-6 py-6 text-xs text-slate-500">Транзакцій ще немає.</div>;
  }

  return (
    <div className="max-h-72 overflow-y-auto divide-y divide-slate-800/50">
      {txs.map(tx => (
        <div key={tx.id} className="px-6 py-3 flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="text-xs text-slate-300 flex items-center gap-1.5">
              <span className="capitalize">{tx.type}</span>
              <span className="text-slate-600">·</span>
              <span className="text-slate-500">{tx.source}</span>
              {tx.linkedOrderId && (
                <span title={`Ордер ${tx.linkedOrderId}`} className="text-accent-500">
                  <Link2 className="w-3 h-3" />
                </span>
              )}
            </div>
            <div className="text-[11px] text-slate-500 font-mono tabular-nums">
              {new Date(tx.timestamp * 1000).toLocaleString('uk-UA')}
            </div>
          </div>

          <div
            className={cn(
              'text-sm font-bold tabular-nums shrink-0',
              tx.direction === 'in' ? 'text-accent-400' : 'text-red-400'
            )}
          >
            {tx.direction === 'in' ? '+' : '−'}{uah(tx.amount)}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Зведення по всіх картках: обіг і кількість транзакцій за період. */
function CardsReport() {
  const { data: report } = useSWR<CardReportRow[]>(
    '/cards/report',
    () => api.getCardsReport(),
    { shouldRetryOnError: false }
  );

  if (!report?.length) return null;

  // Набір метрик залежить від get_card_report_stats — не прибиваємо його
  // цвяхами, а показуємо те, що реально прийшло.
  const metricKeys = Array.from(
    new Set(
      report.flatMap(row =>
        Object.entries(row)
          .filter(([key, v]) => typeof v === 'number' && !BASE_FIELDS.has(key))
          .map(([key]) => key)
      )
    )
  );

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6 overflow-x-auto"
    >
      <h2 className="text-lg font-bold text-white mb-4">Звіт по картках</h2>

      <table className="w-full text-sm min-w-[38rem]">
        <thead>
          <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-800">
            <th className="text-left font-medium py-2 pr-4">Картка</th>
            <th className="text-right font-medium py-2 pr-4">Баланс</th>
            {metricKeys.map(key => (
              <th key={key} className="text-right font-medium py-2 pr-4">{humanize(key)}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {report.map(row => (
            <tr key={row.cardId}>
              <td className="py-2.5 pr-4">
                <span className="font-bold text-white">{row.label || row.bankName}</span>
                <span className="text-slate-500 text-xs"> ·••••{row.lastFour}</span>
              </td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-slate-200">
                {uah(row.balance)}
              </td>
              {metricKeys.map(key => (
                <td key={key} className="py-2.5 pr-4 text-right tabular-nums text-slate-400">
                  {typeof row[key] === 'number' ? Math.round(row[key] as number).toLocaleString('uk-UA') : '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </motion.section>
  );
}

const BASE_FIELDS = new Set(['balance']);

const humanize = (key: string) =>
  key.replace(/([A-Z])/g, ' $1').replace(/^./, c => c.toUpperCase());

function Empty({ text, spinner }: { text: string; spinner?: boolean }) {
  return (
    <div className="flex items-center justify-center gap-2 py-20 text-slate-400 text-sm">
      {spinner && <Loader2 className="w-4 h-4 animate-spin" />}
      {text}
    </div>
  );
}
