import React, { useState } from 'react';
import useSWR from 'swr';
import { Building2, Loader2, Link2, Unlink, Info } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '../../lib/utils';
import { api } from '../../services/api';
import { Bank, BankScopes as BankScopesData, BankSide, ScannerMode } from '../../types';

/**
 * Банки: спільні для всіх режимів плюс винятки для окремого режиму.
 *
 * Навіщо два рівні. Досі колонок було три (загальні, купівля, продаж) і всі
 * спільні, а майстер Taker Buy писав свій вибір просто в buy_bank_codes —
 * те саме поле, яке читає спред. Тобто налаштувавши банки в тейкері, ти
 * мовчки міняв банки купівлі й для спредів.
 *
 * Тепер спільні списки лишились як були (нічого не зламано), а поверх них
 * можна задати виняток для конкретного режиму. Порожній виняток = «беру
 * спільні», тож відкотитись можна в один клік.
 */

const MODE_LABELS: Record<ScannerMode, string> = {
  SPREAD: 'Спред',
  TAKER_BUY: 'Taker Buy',
  TAKER_SELL: 'Taker Sell',
  MAKER_BUY: 'Maker Buy',
  MAKER_SELL: 'Maker Sell',
};

/** Які сторони має сенс налаштовувати в кожному режимі. */
const MODE_SIDES: Record<ScannerMode, BankSide[]> = {
  SPREAD: ['buy', 'sell'],
  TAKER_BUY: ['buy'],
  TAKER_SELL: ['sell'],
  MAKER_BUY: ['buy'],
  MAKER_SELL: ['sell'],
};

const SIDE_LABELS: Record<BankSide, string> = {
  buy: 'Купівля',
  sell: 'Продаж',
};

type Scope = 'shared' | ScannerMode;

interface Props {
  banks: Bank[];
  /** Активні режими — щоб не пропонувати налаштовувати вимкнені. */
  activeModes: ScannerMode[];
  /** Спільні списки редагує батьківська панель через чернетку фільтрів. */
  sharedValue: (side: BankSide | 'general') => string[];
  onToggleShared: (field: 'bankCodes' | 'buyBankCodes' | 'sellBankCodes', code: string) => void;
}

export function BankScopes({ banks, activeModes, sharedValue, onToggleShared }: Props) {
  const [scope, setScope] = useState<Scope>('shared');
  const { data, mutate } = useSWR<BankScopesData>(
    '/user/bank-scopes',
    () => api.getBankScopes(),
    { shouldRetryOnError: false }
  );

  // Показуємо вкладку режиму, лише якщо він увімкнений: налаштовувати
  // банки для того, що не працює, — спосіб забути про це й здивуватись.
  const scopes: Scope[] = ['shared', ...activeModes];
  const active: Scope = scopes.includes(scope) ? scope : 'shared';

  return (
    <div>
      <p className="text-xs text-slate-400 -mt-2 mb-4">
        Спільні списки працюють у всіх режимах. Якщо для якогось режиму
        потрібні інші банки — задай їх на його вкладці; решта режимів
        лишиться на спільних.
      </p>

      <div className="flex flex-wrap gap-1 bg-slate-950 border border-slate-800 rounded-xl p-1 mb-5 w-fit">
        {scopes.map(s => {
          const isShared = s === 'shared';
          const overridden =
            !isShared && MODE_SIDES[s as ScannerMode]
              .some(side => data?.resolved?.[s as ScannerMode]?.[side]?.isOverride);
          return (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-colors',
                active === s ? 'bg-slate-800 text-white' : 'text-slate-500 hover:text-slate-300'
              )}
            >
              {isShared ? 'Спільні' : MODE_LABELS[s as ScannerMode]}
              {overridden && <span className="w-1.5 h-1.5 rounded-full bg-accent-400" />}
            </button>
          );
        })}
      </div>

      {active === 'shared' ? (
        <SharedLists banks={banks} value={sharedValue} onToggle={onToggleShared} />
      ) : (
        <ModeOverride
          banks={banks}
          mode={active as ScannerMode}
          data={data}
          onSaved={mutate}
        />
      )}
    </div>
  );
}

function SharedLists({
  banks, value, onToggle,
}: {
  banks: Bank[];
  value: (side: BankSide | 'general') => string[];
  onToggle: (field: 'bankCodes' | 'buyBankCodes' | 'sellBankCodes', code: string) => void;
}) {
  return (
    <div className="space-y-5">
      <Picker
        title="Загальні"
        hint="Використовуються, коли окремі списки купівлі й продажу порожні"
        banks={banks}
        selected={value('general')}
        onToggle={code => onToggle('bankCodes', code)}
      />
      <Picker
        title="Тільки для купівлі"
        banks={banks}
        selected={value('buy')}
        onToggle={code => onToggle('buyBankCodes', code)}
      />
      <Picker
        title="Тільки для продажу"
        banks={banks}
        selected={value('sell')}
        onToggle={code => onToggle('sellBankCodes', code)}
      />
      <p className="flex items-start gap-1.5 text-[11px] text-slate-500 leading-snug">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        Зміни тут зберігаються кнопкою «Зберегти» вгорі сторінки — разом з
        рештою фільтрів.
      </p>
    </div>
  );
}

function ModeOverride({
  banks, mode, data, onSaved,
}: {
  banks: Bank[];
  mode: ScannerMode;
  data?: BankScopesData;
  onSaved: () => void;
}) {
  const [saving, setSaving] = useState<BankSide | null>(null);

  const save = async (side: BankSide, next: string[]) => {
    setSaving(side);
    try {
      await api.setBankScope(mode, side, next);
      toast.success(
        next.length
          ? `${MODE_LABELS[mode]} · ${SIDE_LABELS[side].toLowerCase()}: свої банки`
          : `${MODE_LABELS[mode]} · ${SIDE_LABELS[side].toLowerCase()}: повернуто спільні`
      );
      onSaved();
    } catch (e: any) {
      toast.error(`Не збережено: ${e?.message ?? 'помилка'}`);
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-5">
      {MODE_SIDES[mode].map(side => {
        const resolved = data?.resolved?.[mode]?.[side];
        const isOverride = Boolean(resolved?.isOverride);
        const current = resolved?.banks ?? [];

        return (
          <div key={side}>
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-bold text-white">{SIDE_LABELS[side]}</span>
                <span
                  className={cn(
                    'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border',
                    isOverride
                      ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                      : 'bg-slate-950 border-slate-800 text-slate-500'
                  )}
                  title={
                    isOverride
                      ? 'Цей режим використовує власний список'
                      : 'Цей режим бере спільні банки'
                  }
                >
                  {isOverride
                    ? <><Unlink className="w-3 h-3" /> свої</>
                    : <><Link2 className="w-3 h-3" /> спільні</>}
                </span>
              </div>

              {isOverride && (
                <button
                  onClick={() => save(side, [])}
                  disabled={saving === side}
                  className="text-[11px] font-bold text-slate-400 hover:text-white transition-colors disabled:opacity-40"
                >
                  повернути спільні
                </button>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              {banks.map(bank => {
                const on = current.includes(bank.code);
                return (
                  <button
                    key={bank.code}
                    disabled={saving === side}
                    onClick={() => {
                      const next = on
                        ? current.filter(c => c !== bank.code)
                        : [...current, bank.code];
                      save(side, next);
                    }}
                    title={`код ${bank.code}`}
                    className={cn(
                      'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all disabled:opacity-50',
                      on
                        ? 'bg-purple-500/10 border-purple-500/30 text-purple-300'
                        : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
                    )}
                  >
                    {bank.name}
                  </button>
                );
              })}
              {saving === side && <Loader2 className="w-4 h-4 animate-spin text-slate-500 self-center" />}
            </div>

            {!isOverride && (
              <p className="text-[11px] text-slate-500 mt-2">
                Клік по банку створить власний список саме для цього режиму —
                спільні списки лишаться без змін.
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Picker({
  title, hint, banks, selected, onToggle,
}: {
  title: string;
  hint?: string;
  banks: Bank[];
  selected: string[];
  onToggle: (code: string) => void;
}) {
  const list = selected || [];
  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <span className="text-sm font-bold text-white">{title}</span>
        <span className="text-xs text-slate-500">
          {list.length ? `обрано ${list.length}` : 'порожньо'}
        </span>
      </div>
      {hint && <div className="text-[11px] text-slate-500 mb-2">{hint}</div>}
      <div className="flex flex-wrap gap-2">
        {banks.map(bank => (
          <button
            key={bank.code}
            onClick={() => onToggle(bank.code)}
            title={`код ${bank.code}`}
            className={cn(
              'px-3 py-1.5 rounded-lg text-xs font-bold border transition-all',
              list.includes(bank.code)
                ? 'bg-purple-500/10 border-purple-500/30 text-purple-300'
                : 'bg-slate-950 border-slate-800 text-slate-500 hover:border-slate-700'
            )}
          >
            {bank.name}
          </button>
        ))}
      </div>
    </div>
  );
}
