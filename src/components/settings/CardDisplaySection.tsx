import React from 'react';
import useSWR from 'swr';
import { motion } from 'motion/react';
import { CreditCard, Loader2, Info, Send } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import {
  CardDetailLevel, CardDisplaySettings, CardOutputMode, CardSplitMode,
  ShowRejectedOrders,
} from '../../types';

/**
 * Картковий модуль виводу — те саме меню, що в боті під «Налаштування
 * карткового модуля». Керує тим, як картки показуються в алертах сканера.
 *
 * Бекенд зливає частковий payload із поточним станом: репозиторій робить
 * UPSERT усього рядка з дефолтами, тож без merge зміна одного поля
 * скидала б решту.
 */
/**
 * Повний набір режимів. Який із них показати — вирішує бек полем
 * `availableSplitModes`: міжбанк живе за експериментальною фічею, і
 * пропонувати його завжди означало б отримати 400 у відповідь.
 */
const SPLIT_OPTIONS: { value: CardSplitMode; label: string; hint: string }[] = [
  { value: 'off', label: 'Вимкнено', hint: 'Тільки одна картка й один переказ' },
  { value: 'intra_bank', label: 'У межах банку', hint: 'Кілька карток одного банку' },
  {
    value: 'inter_bank',
    label: 'Між банками',
    hint: 'Збирати суму з карток різних банків — потребує узгодження в чаті',
  },
];

export default function CardDisplaySection() {
  const { data, error, isLoading, mutate } = useSWR<CardDisplaySettings>(
    '/user/card-display',
    () => api.getCardDisplay(),
    { shouldRetryOnError: false }
  );

  if (isLoading) return <Shell><Muted spinner>Читаю налаштування карток…</Muted></Shell>;
  if (error || !data) {
    return <Shell><Muted>Не вдалось завантажити: {(error as Error)?.message ?? 'немає даних'}</Muted></Shell>;
  }

  const patch = async (key: keyof CardDisplaySettings, value: unknown) => {
    mutate({ ...data, [key]: value }, false);
    try {
      await api.updateCardDisplay({ [key]: value } as Partial<CardDisplaySettings>);
      await mutate();
    } catch (e: any) {
      await mutate();
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    }
  };

  const isOff = data.cardModuleMode === 'off';

  return (
    <Shell>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <CreditCard className="w-5 h-5 text-accent-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Картковий модуль</h2>
            <p className="text-xs text-slate-400">Як показувати картки в алертах сканера</p>
          </div>
        </div>

        <Switch
          checked={!isOff}
          onChange={v => patch('cardModuleMode', v ? 'on' : 'off')}
        />
      </div>

      {isOff ? (
        <Muted>
          Модуль вимкнено — картки в алертах не з'являються, і звірка лімітів не працює.
        </Muted>
      ) : (
        <div className="space-y-6">
          <Field label="Деталізація" sub="card_detail_level">
            <Picker<CardDetailLevel>
              value={data.cardDetailLevel}
              onChange={v => patch('cardDetailLevel', v)}
              options={[
                { value: 'full', label: 'Повна', hint: 'Усі ліміти банку' },
                { value: 'compact', label: 'Компактна', hint: 'Лише баланси й добовий залишок' },
              ]}
            />
          </Field>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Toggle
              label="В окремих режимах"
              hint="Показувати картки і в тейкер/мейкер-режимах, не лише у спреді"
              checked={data.enableInSingleModes}
              onChange={v => patch('enableInSingleModes', v)}
            />
            <Toggle
              label="Розбивка балансів"
              hint="Показувати баланс кожної картки окремо"
              checked={data.showBalancesBreakdown}
              onChange={v => patch('showBalancesBreakdown', v)}
            />
            <Toggle
              label="Поради щодо лімітів"
              hint="Підказувати, на яку картку краще приймати"
              checked={data.showTransferTips}
              onChange={v => patch('showTransferTips', v)}
            />
          </div>

          <Field label="Ліміт непрогрітих (₴)" sub="cold_card_limit">
            <input
              type="number"
              step={500}
              value={data.coldCardLimit}
              onChange={e => patch('coldCardLimit', parseFloat(e.target.value) || 0)}
              className="w-40 bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-accent-500 outline-none tabular-nums"
            />
            <Hint>Скільки максимум пропускати через картку, яка ще не «прогріта» обігом.</Hint>
          </Field>

          <div className="pt-2 border-t border-slate-800/60">
            <h3 className="text-sm font-bold text-white mb-4">Складання суми під ордер</h3>

            <div className="space-y-6">
              <Field label="Спліт між картками" sub="card_split_mode">
                <Picker<CardSplitMode>
                  value={data.cardSplitMode}
                  onChange={v => patch('cardSplitMode', v)}
                  options={SPLIT_OPTIONS.filter(o =>
                    (data.availableSplitModes ?? ['off', 'intra_bank']).includes(o.value)
                  )}
                />
                {!(data.availableSplitModes ?? []).includes('inter_bank') && (
                  <Hint>
                    Набір суми з карток <b>різних</b> банків — експеримент, і
                    вмикається окремо: «Можливості» → «Картки та маршрути» →
                    «Кошики карток між банками». Саме через це обмеження
                    31 000 ₴ на трьох картках перетворювались на 21 000 ₴
                    доступних.
                  </Hint>
                )}
              </Field>

              <Field label="Карток на угоду" sub="max_cards_per_order">
                <Picker<string>
                  value={String(data.maxCardsPerOrder)}
                  onChange={v => patch('maxCardsPerOrder', Number(v))}
                  options={[
                    { value: '1', label: '1', hint: 'Один переказ — найменше питань' },
                    { value: '2', label: '2', hint: 'Два перекази' },
                    { value: '3', label: '3', hint: 'Три перекази' },
                  ]}
                />
                <Hint>
                  Таймер угоди — зазвичай 15 хвилин, і кожен зайвий переказ
                  через ще один застосунок це реальний ризик апеляції, а не
                  просто «повільніше».
                </Hint>
              </Field>

              <Field label="Відкинуті ордери" sub="show_rejected_orders">
                <Picker<ShowRejectedOrders>
                  value={data.showRejectedOrders}
                  onChange={v => patch('showRejectedOrders', v)}
                  options={[
                    {
                      value: 'with_reason',
                      label: 'Показувати з причиною',
                      hint: 'Видно, що ордер був і чому не пройшов',
                    },
                    { value: 'hide', label: 'Ховати', hint: 'Тільки те, що пройшло' },
                  ]}
                />
                <Hint>
                  Порожній список сам по собі не каже, ринку немає чи карток
                  не вистачило.
                </Hint>
              </Field>
            </div>
          </div>

          {/* Те, що описує форму повідомлення, а не його зміст.
              «Окремою відповіддю» і «спойлер» — поняття Telegram: на
              сторінці картковий блок стоїть просто в ордері й розгортається
              кліком, тож ці перемикачі там нічого не міняють. */}
          <div className="pt-4 border-t border-slate-800/60">
            <div className="flex items-center gap-2 mb-1">
              <Send className="w-3.5 h-3.5 text-slate-500" />
              <h3 className="text-sm font-bold text-white">Тільки для Telegram</h3>
            </div>
            <p className="text-[11px] text-slate-500 mb-3 leading-snug">
              На сайті картки показуються всередині ордера й розгортаються
              кліком — окремого повідомлення й спойлера там немає.
            </p>

            <div className="space-y-4">
              <Field label="Вивід карток" sub="card_output_mode">
                <Picker<CardOutputMode>
                  value={data.cardOutputMode}
                  onChange={v => patch('cardOutputMode', v)}
                  options={[
                    { value: 'inline', label: 'У тексті спреду', hint: 'Вбудувати прямо в повідомлення' },
                    { value: 'reply', label: 'Окремою відповіддю', hint: 'Надіслати Reply на алерт' },
                  ]}
                />
              </Field>

              <Toggle
                label="Смарт-спойлер"
                hint="Автоматично розгортати картку при загрозі фінмоніторингу"
                checked={data.enableSmartSpoiler}
                onChange={v => patch('enableSmartSpoiler', v)}
              />
            </div>
          </div>
        </div>
      )}
    </Shell>
  );
}

// ─── Дрібниці ─────────────────────────────────────────────────────────────

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

function Field({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm font-bold text-white mb-0.5">{label}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono mb-2">{sub}</div>}
      {children}
    </div>
  );
}

function Picker<T extends string>({
  value, onChange, options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string; hint: string }[];
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(o => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          title={o.hint}
          className={cn(
            'px-4 py-2 rounded-xl text-xs font-bold border transition-all',
            value === o.value
              ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
              : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

const Toggle: React.FC<{
  label: string; hint: string; checked: boolean; onChange: (v: boolean) => void;
}> = ({ label, hint, checked, onChange }) => (
  <button
    onClick={() => onChange(!checked)}
    className={cn(
      'flex items-start gap-3 p-4 rounded-2xl border text-left transition-all',
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

function Switch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className={cn(
        'w-12 h-6 rounded-full relative transition-colors shrink-0',
        checked ? 'bg-accent-500' : 'bg-slate-700'
      )}
    >
      <div className={cn(
        'absolute top-1 w-4 h-4 rounded-full bg-white transition-all',
        checked ? 'right-1' : 'left-1'
      )} />
    </button>
  );
}

function Muted({ children, spinner }: { children: React.ReactNode; spinner?: boolean }) {
  return (
    <p className="flex items-center gap-2 text-sm text-slate-500">
      {spinner && <Loader2 className="w-4 h-4 animate-spin" />}
      {children}
    </p>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 mt-2 text-[11px] text-slate-500 leading-snug">
      <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
      <span>{children}</span>
    </div>
  );
}
