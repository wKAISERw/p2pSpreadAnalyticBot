import React, { useState } from 'react';
import useSWR from 'swr';
import { Filter, Info, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '../../services/api';
import {
  CardDisplaySettings, RejectionStats as Stats, RejectionStatsRow,
} from '../../types';
import { cn } from '../../lib/utils';

const uah = (n: number) =>
  `${Math.round(n).toLocaleString('uk-UA').replace(/,/g, ' ')} ₴`;

/**
 * Чому картковий модуль не пропускав ордери — те саме, що /card_rejections
 * у боті.
 *
 * Причини відмов існували й раніше, але жили вільним текстом усередині
 * движка й нікуди не виводились. Типовий сценарій: користувач бачить ордер
 * у стакані, бот його не показує, і дізнатись чому неможливо.
 *
 * Практична цінність не в самому списку, а в тому, яка причина переважає:
 * «Не вистачає балансу» і «Немає картки цього банку» ведуть до
 * протилежних рішень — у першому випадку має сенс набирати суму з кількох
 * банків, у другому жоден алгоритм не допоможе, треба інші банки.
 */
export function RejectionStats() {
  const [days, setDays] = useState(7);

  const { data, error, isLoading, mutate } = useSWR<Stats>(
    ['/taker/rejections', days],
    () => api.getTakerRejections(days),
    { refreshInterval: 60000, shouldRetryOnError: false }
  );

  if (error) return null;

  const rows = data?.codes ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="bg-slate-900/50 border border-slate-800/50 rounded-2xl p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-slate-400" />
          <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
            Чому ордери не проходять
          </span>
        </div>

        <div className="flex gap-1">
          {[1, 7, 14].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={cn(
                'px-2.5 py-1 rounded-lg text-xs font-bold transition-colors',
                days === d
                  ? 'bg-accent-500 text-slate-950'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              )}
            >
              {d === 1 ? 'доба' : `${d} дн`}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-500">Читаю статистику…</p>
      ) : total === 0 ? (
        <p className="text-sm text-slate-500">
          За цей період картки не відсіяли жодного ордера. Якщо сканер щойно
          запущено — статистика ще накопичується.
        </p>
      ) : (
        <>
          <p className="text-xs text-slate-500 mb-3">
            {total.toLocaleString('uk-UA')} відсіяних ордерів
            {data?.since ? ` з ${data.since}` : ''}. Один ордер рахується раз
            на добу, скільки б кіл не зробив сканер.
          </p>

          <div className="space-y-2">
            {rows.map((row) => (
              <RejectionRow key={row.code} row={row} />
            ))}
          </div>

          <Observations rows={data?.observations ?? []} />
          <Verdict rows={rows} onFixed={() => mutate()} />
        </>
      )}
    </div>
  );
}

/**
 * Ордери, які пройшли, але не такими, як задумано.
 *
 * У частку відмов не входять свідомо: інакше вийшло б «90% відмов» по
 * ордерах, які людина насправді отримала. Але саме ці рядки відповідають
 * на питання, чи варті кошики між банками — вони рахують, скільки разів
 * стеля одного банку коштувала обсягу.
 */
function Observations({ rows }: { rows: RejectionStatsRow[] }) {
  if (rows.length === 0) return null;

  return (
    <div className="mt-4 pt-3 border-t border-slate-800">
      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-600 mb-2">
        Пройшли, але не так
      </div>
      {rows.map((row) => (
        <div key={row.code} className="flex items-baseline gap-2 text-[11px] mb-1">
          <span className="tabular-nums text-slate-500 shrink-0">{row.hits}×</span>
          <span className="text-slate-400 leading-snug">
            {row.title}
            {row.avgShortfall > 0 && (
              <span className="text-slate-500">
                {' '}— у середньому недобрано {uah(row.avgShortfall)}
              </span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

const RejectionRow: React.FC<{ row: RejectionStatsRow }> = ({ row }) => {
  const { title, hits, sharePct, avgShortfall, banks } = row;

  return (
    <div className="rounded-xl bg-slate-800/40 border border-slate-800 p-3">
      <div className="flex items-baseline justify-between gap-3 mb-1.5">
        <span className="text-sm font-bold text-slate-200">{title}</span>
        <span className="text-xs tabular-nums text-slate-400 shrink-0">
          {hits.toLocaleString('uk-UA')} · {sharePct}%
        </span>
      </div>

      <div className="h-1.5 rounded-full bg-slate-900 overflow-hidden">
        <div
          className="h-full bg-accent-500/70 rounded-full"
          style={{ width: `${Math.min(100, sharePct)}%` }}
        />
      </div>

      {(avgShortfall > 0 || banks.length > 0) && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-slate-500">
          {avgShortfall > 0 && <span>у середньому бракує {uah(avgShortfall)}</span>}
          {banks.length > 0 && <span>банки: {banks.join(', ')}</span>}
        </div>
      )}
    </div>
  );
};

/**
 * Підказка, що робити з цифрами.
 *
 * Сама таблиця нічого не радить, а розвилка тут проста і дорога: збирати
 * суму з карток різних банків має сенс лише тоді, коли переважає нестача
 * балансу. Якщо мерчанти просто приймають не ті банки — жоден матчинг не
 * допоможе, і писати його марно.
 */
function Verdict({
  rows, onFixed,
}: {
  rows: RejectionStatsRow[];
  onFixed: () => void;
}) {
  // Порада мусить знати, що вже ввімкнено: радити «увімкни кошики» тому,
  // хто їх увімкнув, — це опис старої поведінки движка, поданий як підказка.
  const { data: display, mutate: mutateDisplay } = useSWR<CardDisplaySettings>(
    '/user/card-display',
    () => api.getCardDisplay(),
    { shouldRetryOnError: false, revalidateOnFocus: false }
  );
  const [busy, setBusy] = useState(false);

  const interBank = display?.cardSplitMode === 'inter_bank';
  const canInterBank = (display?.availableSplitModes ?? []).includes('inter_bank');

  const top = rows[0];
  if (!top || top.sharePct < 40) return null;

  // Гроші є, а заважає перемикач. Тут доречна не порада, а сама дія.
  if (top.code === 'split_needs_inter_bank') {
    const enable = async () => {
      setBusy(true);
      try {
        await api.updateCardDisplay({ cardSplitMode: 'inter_bank' });
        await mutateDisplay();
        onFixed();
        toast.success('Спліт між банками увімкнено');
      } catch (e: any) {
        toast.error(`Не вдалось: ${e?.message ?? 'помилка'}`);
      } finally {
        setBusy(false);
      }
    };

    return (
      <Advice>
        <p className="mb-2">
          Грошей вистачає, але вони на різних банках, а режим спліту — «у
          межах одного банку». Кошик між банками при цьому не запускається
          взагалі.
        </p>
        {canInterBank ? (
          <button
            onClick={enable}
            disabled={busy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-500 hover:bg-accent-400 disabled:opacity-50 text-slate-950 text-[11px] font-bold transition-colors"
          >
            {busy && <Loader2 className="w-3 h-3 animate-spin" />}
            Увімкнути спліт між банками
          </button>
        ) : (
          <span>
            Спершу ввімкни фічу: «Можливості» → «Картки та маршрути» →
            «Кошики карток між банками».
          </span>
        )}
      </Advice>
    );
  }

  const hint =
    top.code === 'split_needs_more_cards'
      ? 'Зібрати суму можна, але карток потрібно більше, ніж дозволено. Підніми «Карток на угоду» в налаштуваннях карткового модуля — і врахуй, що кожен зайвий переказ під 15-хвилинний таймер це ризик апеляції.'
      : top.code === 'split_disabled'
        ? 'Спліт вимкнено вручну — движок бере рівно одну картку. Якщо це не навмисно, увімкни «У межах банку» або «Між банками» в налаштуваннях карток.'
        : top.code === 'unknown_bank_code'
          ? 'Мерчанти приймають банк, якого немає в реєстрі бота — картка під нього не підбереться, скільки б їх не додати. Це не про твої налаштування: банк треба додати в реєстр.'
          : top.code === 'below_merchant_min'
            ? 'Сума набирається, але вона менша за мінімум мерчанта. Тут або більший залишок на картках, або м’якший фільтр по мінімальному ліміту ордера.'
            : top.code === 'insufficient_balance' || top.code === 'limits_exhausted'
              ? interBank
                ? 'Гроші не набираються навіть з усіх карток разом — кошики між банками вже ввімкнені. Далі допоможе тільки поповнення або менший обсяг угоди.'
                : 'Гроші є, але не на одній картці. Саме той випадок, коли допомагають кошики між банками: «Можливості» → «Картки та маршрути».'
              : top.code === 'no_cards_for_bank'
                ? 'Мерчанти приймають банки, яких у тебе немає. Тут допоможуть нові картки або ширший список банків у фільтрах, а не налаштування матчингу.'
                : top.code === 'no_active_cards'
                  ? 'Картки цього банку є, але жодна не активна — найчастіше заморожені кошти. Перевір статус у списку карток.'
                  : top.code === 'cold_card'
                    ? 'Картки ще не пройшли прогрів. Це минеться саме — або підніми стелю прогріву в налаштуваннях, якщо картка насправді не нова.'
                    : top.code === 'max_tx_per_day' || top.code === 'cooldown'
                      ? 'Упираєшся не в гроші, а в частоту переказів. Більше карток того ж банку дало б більше слотів.'
                      : null;

  if (!hint) return null;
  return <Advice>{hint}</Advice>;
}

function Advice({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-2.5 mt-4 p-3 rounded-xl bg-slate-800/30 border border-slate-800">
      <Info className="w-4 h-4 text-slate-500 shrink-0 mt-0.5" />
      <div className="text-xs text-slate-400 leading-relaxed">{children}</div>
    </div>
  );
}
