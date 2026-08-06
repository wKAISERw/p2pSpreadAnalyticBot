import React from 'react';
import { TrendingDown, TrendingUp, Zap, Gauge, Wallet, Info } from 'lucide-react';
import { cn } from '../../lib/utils';
import {
  BuyBalanceMode, BuyPriceStrategy, SellPriceStrategy, TakerSpeed, UserFilters, UserFiltersPatch,
} from '../../types';

/**
 * Налаштування тейкер-режимів.
 *
 * Значення один в один відповідають тому, що читає
 * core/engine/taker_scanner.py — розходження тут означало б фільтр,
 * який ніколи не спрацює.
 */

const SELL_STRATEGIES: { value: SellPriceStrategy; label: string; hint: string }[] = [
  { value: 'roi', label: 'Від ROI', hint: 'Беремо все, що не дешевше за мінімальну ціну' },
  { value: 'min', label: 'Від мінімуму', hint: 'Те саме, але поріг задається вручну' },
  { value: 'range', label: 'Діапазон', hint: 'Ціна між мінімумом і максимумом' },
  { value: 'exact', label: 'Точна ціна', hint: 'Збіг із точністю до півкопійки' },
  { value: 'any', label: 'Будь-яка', hint: 'Без цінового фільтра' },
];

const BUY_STRATEGIES: { value: BuyPriceStrategy; label: string; hint: string }[] = [
  { value: 'max', label: 'До максимуму', hint: 'Беремо все, що не дорожче за поріг' },
  { value: 'range', label: 'Діапазон', hint: 'Ціна між «від» і «до»' },
  { value: 'exact', label: 'Точна ціна', hint: 'Збіг із точністю до півкопійки' },
  { value: 'any', label: 'Будь-яка', hint: 'Без цінового фільтра' },
];

const BALANCE_MODES: { value: BuyBalanceMode; label: string; hint: string }[] = [
  { value: 'CARD_ENFORCED', label: 'За лімітами карток', hint: 'Не пропонувати більше, ніж дозволяють ліміти' },
  { value: 'AUTO_SCALE', label: 'Авто-підбір суми', hint: 'Підганяти обсяг під доступний баланс' },
  { value: 'FREE', label: 'Без обмежень', hint: 'Ігнорувати баланс карток' },
];

interface Props {
  mode: 'TAKER_BUY' | 'TAKER_SELL';
  filters: UserFilters & Record<string, any>;
  value: <K extends keyof UserFiltersPatch>(key: K, fallback: any) => any;
  set: <K extends keyof UserFiltersPatch>(key: K, v: UserFiltersPatch[K]) => void;
  exchangeNames: string[];
}

export function TakerSettings({ mode, filters, value, set, exchangeNames }: Props) {
  return mode === 'TAKER_SELL'
    ? <TakerSell filters={filters} value={value} set={set} exchangeNames={exchangeNames} />
    : <TakerBuy filters={filters} value={value} set={set} />;
}

function TakerSell({ filters, value, set, exchangeNames }: Omit<Props, 'mode'>) {
  const strategy = value('takerSellPriceStrategy', filters.takerSellPriceStrategy ?? 'roi');
  const speed = value('takerSellSpeed', filters.takerSellSpeed ?? 'ANY');

  return (
    <Card
      icon={<TrendingUp className="w-5 h-5 text-accent-400" />}
      title="Taker Sell"
      subtitle="Полювання на чужі оголошення про купівлю — ти продаєш USDT"
    >
      <Grid>
        <Field label="Обсяг на продаж (USDT)" sub="taker_sell_amount">
          <Num value={value('takerSellAmount', filters.takerSellAmount)} onChange={v => set('takerSellAmount', v)} step={10} />
        </Field>
        <Field label="Ціна закупівлі (₴)" sub="taker_sell_price · для розрахунку профіту">
          <Num value={value('takerSellPrice', filters.takerSellPrice)} onChange={v => set('takerSellPrice', v)} step={0.01} />
        </Field>
        <Field label="Бажаний профіт (%)" sub="taker_sell_profit">
          <Num value={value('takerSellProfit', filters.takerSellProfit)} onChange={v => set('takerSellProfit', v)} step={0.1} />
        </Field>
      </Grid>

      <Divider />

      <Field label="Цінова стратегія" sub="taker_sell_price_strategy">
        <ChipRow
          options={SELL_STRATEGIES}
          active={strategy}
          onPick={v => set('takerSellPriceStrategy', v as SellPriceStrategy)}
        />
      </Field>

      {strategy !== 'any' && (
        <Grid>
          <Field
            label={strategy === 'exact' ? 'Точна ціна (₴)' : 'Мінімальна ціна (₴)'}
            sub="taker_sell_min_price"
          >
            <Num value={value('takerSellMinPrice', filters.takerSellMinPrice)} onChange={v => set('takerSellMinPrice', v)} step={0.01} />
          </Field>
          {strategy === 'range' && (
            <Field label="Максимальна ціна (₴)" sub="taker_sell_price_to">
              <Num value={value('takerSellPriceTo', filters.takerSellPriceTo)} onChange={v => set('takerSellPriceTo', v)} step={0.01} />
            </Field>
          )}
        </Grid>
      )}

      <Divider />

      <Field label="Швидкість" sub="taker_sell_speed">
        <SpeedPicker value={speed} onPick={v => set('takerSellSpeed', v)} />
      </Field>

      <Field label="Біржа" sub="taker_sell_exchange · порожньо = всі">
        <div className="flex flex-wrap gap-2">
          <Chip
            label="Всі"
            active={!value('takerSellExchange', filters.takerSellExchange)}
            onClick={() => set('takerSellExchange', '')}
          />
          {exchangeNames.map(name => (
            <Chip
              key={name}
              label={name}
              active={value('takerSellExchange', filters.takerSellExchange) === name}
              onClick={() => set('takerSellExchange', name)}
            />
          ))}
        </div>
      </Field>
    </Card>
  );
}

function TakerBuy({ filters, value, set }: Omit<Props, 'mode' | 'exchangeNames'>) {
  const strategy = value('takerBuyPriceStrategy', filters.takerBuyPriceStrategy ?? 'any');
  const speed = value('takerBuySpeed', filters.takerBuySpeed ?? 'ANY');
  const balanceMode = value('buyBalanceMode', filters.buyBalanceMode ?? 'CARD_ENFORCED');

  return (
    <Card
      icon={<TrendingDown className="w-5 h-5 text-blue-400" />}
      title="Taker Buy"
      subtitle="Полювання на чужі оголошення про продаж — ти купуєш USDT"
    >
      <Grid>
        <Field label="Обсяг на купівлю (USDT)" sub="taker_buy_amount">
          <Num value={value('takerBuyAmount', filters.takerBuyAmount)} onChange={v => set('takerBuyAmount', v)} step={10} />
        </Field>
      </Grid>

      <Divider />

      <Field label="Цінова стратегія" sub="taker_buy_price_strategy">
        <ChipRow
          options={BUY_STRATEGIES}
          active={strategy}
          onPick={v => set('takerBuyPriceStrategy', v as BuyPriceStrategy)}
        />
      </Field>

      {strategy !== 'any' && (
        <Grid>
          {strategy === 'range' && (
            <Field label="Ціна від (₴)" sub="taker_buy_price_from">
              <Num value={value('takerBuyPriceFrom', filters.takerBuyPriceFrom)} onChange={v => set('takerBuyPriceFrom', v)} step={0.01} />
            </Field>
          )}
          <Field
            label={strategy === 'exact' ? 'Точна ціна (₴)' : 'Максимальна ціна (₴)'}
            sub="taker_buy_max_price"
          >
            <Num value={value('takerBuyMaxPrice', filters.takerBuyMaxPrice)} onChange={v => set('takerBuyMaxPrice', v)} step={0.01} />
          </Field>
        </Grid>
      )}

      <Divider />

      <Field label="Фіатні ліміти оголошення (₴)" sub="taker_buy_limit_min / taker_buy_limit_max · 0 = без обмеження">
        <div className="grid grid-cols-2 gap-3">
          <Num value={value('takerBuyLimitMin', filters.takerBuyLimitMin)} onChange={v => set('takerBuyLimitMin', v)} step={100} />
          <Num value={value('takerBuyLimitMax', filters.takerBuyLimitMax)} onChange={v => set('takerBuyLimitMax', v)} step={100} />
        </div>
      </Field>

      <Field label="Швидкість" sub="taker_buy_speed">
        <SpeedPicker value={speed} onPick={v => set('takerBuySpeed', v)} />
      </Field>

      <Divider />

      <Field label="Звірка з балансом карток" sub="buy_balance_mode">
        <ChipRow
          options={BALANCE_MODES}
          active={balanceMode}
          onPick={v => set('buyBalanceMode', v as BuyBalanceMode)}
        />
      </Field>

      {balanceMode === 'AUTO_SCALE' && (
        <div className="flex flex-wrap gap-3 pl-1">
          <Toggle
            label="Зменшувати суму"
            hint="Якщо на картках менше, ніж заплановано"
            checked={Boolean(value('buyAutoScaleDown', filters.buyAutoScaleDown ?? 1))}
            onChange={v => set('buyAutoScaleDown', v ? 1 : 0)}
          />
          <Toggle
            label="Збільшувати суму"
            hint="Якщо на картках з'явилось більше"
            checked={Boolean(value('buyAutoScaleUp', filters.buyAutoScaleUp ?? 1))}
            onChange={v => set('buyAutoScaleUp', v ? 1 : 0)}
          />
        </div>
      )}
    </Card>
  );
}

// ─── Будівельні блоки ─────────────────────────────────────────────────────

function Card({
  icon, title, subtitle, children,
}: {
  icon: React.ReactNode; title: string; subtitle: string; children: React.ReactNode;
}) {
  return (
    <section className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6">
      <div className="flex items-center gap-3 mb-5">
        <div className="p-2 bg-slate-800/60 rounded-xl">{icon}</div>
        <div>
          <h2 className="text-lg font-bold text-white">{title}</h2>
          <p className="text-xs text-slate-400">{subtitle}</p>
        </div>
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">{children}</div>;
}

function Divider() {
  return <div className="h-px bg-slate-800/70" />;
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

function Num({ value, onChange, step }: { value: number; onChange: (v: number) => void; step: number }) {
  return (
    <input
      type="number"
      step={step}
      value={Number.isFinite(value) ? value : 0}
      onChange={e => onChange(parseFloat(e.target.value) || 0)}
      className="w-full bg-slate-950 border border-slate-800 rounded-xl px-4 py-2.5 text-sm font-bold text-white focus:border-accent-500 focus:ring-2 focus:ring-accent-500/50 outline-none transition-all tabular-nums"
    />
  );
}

const Chip: React.FC<{ label: string; active: boolean; onClick: () => void; hint?: string }> = ({
  label, active, onClick, hint,
}) => (
  <button
    onClick={onClick}
    title={hint}
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

function ChipRow({
  options, active, onPick,
}: {
  options: { value: string; label: string; hint: string }[];
  active: string;
  onPick: (v: string) => void;
}) {
  const current = options.find(o => o.value === active);
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {options.map(o => (
          <Chip key={o.value} label={o.label} hint={o.hint} active={active === o.value} onClick={() => onPick(o.value)} />
        ))}
      </div>
      {current && (
        <div className="flex items-center gap-1.5 mt-2 text-[11px] text-slate-500">
          <Info className="w-3 h-3 shrink-0" />
          {current.hint}
        </div>
      )}
    </div>
  );
}

function SpeedPicker({ value, onPick }: { value: TakerSpeed; onPick: (v: TakerSpeed) => void }) {
  const options: { value: TakerSpeed; label: string; icon: React.ReactNode; hint: string }[] = [
    { value: 'FAST', label: 'FAST', icon: <Zap className="w-3.5 h-3.5" />, hint: 'Ордер має вміщати всю суму цілком' },
    { value: 'ANY', label: 'ANY', icon: <Gauge className="w-3.5 h-3.5" />, hint: 'Достатньо, щоб проходив мінімальний ліміт' },
  ];
  const current = options.find(o => o.value === value);

  return (
    <div>
      <div className="flex gap-2">
        {options.map(o => (
          <button
            key={o.value}
            onClick={() => onPick(o.value)}
            title={o.hint}
            className={cn(
              'flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-bold border transition-all',
              value === o.value
                ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
                : 'bg-slate-950 border-slate-800 text-slate-400 hover:border-slate-700'
            )}
          >
            {o.icon}{o.label}
          </button>
        ))}
      </div>
      {current && (
        <div className="flex items-center gap-1.5 mt-2 text-[11px] text-slate-500">
          <Info className="w-3 h-3 shrink-0" />
          {current.hint}
        </div>
      )}
    </div>
  );
}

function Toggle({
  label, hint, checked, onChange,
}: {
  label: string; hint: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <button
      onClick={() => onChange(!checked)}
      title={hint}
      className={cn(
        'flex items-center gap-2.5 px-4 py-2.5 rounded-xl border transition-all',
        checked
          ? 'bg-accent-500/10 border-accent-500/30'
          : 'bg-slate-950 border-slate-800 hover:border-slate-700'
      )}
    >
      <div className={cn('w-8 h-4 rounded-full relative transition-colors shrink-0', checked ? 'bg-accent-500' : 'bg-slate-600')}>
        <div className={cn('absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all', checked ? 'right-0.5' : 'left-0.5')} />
      </div>
      <div className="text-left">
        <div className={cn('text-xs font-bold', checked ? 'text-accent-400' : 'text-slate-300')}>{label}</div>
        <div className="text-[10px] text-slate-500">{hint}</div>
      </div>
    </button>
  );
}

export { Wallet };
