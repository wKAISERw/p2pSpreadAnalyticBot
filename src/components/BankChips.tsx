import React from 'react';
import useSWR from 'swr';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { Bank, BankRef, Card } from '../types';
import { useBankProfiles } from '../hooks/useBankProfiles';
import { cn } from '../lib/utils';

/**
 * Банки, які приймає мерчант, із підсвіткою тих, під які картка вже є.
 *
 * Під біржами довго стояли сирі коди — «328 → 43». Прочитати їх могла
 * тільки людина, яка тримає мапу кодів у голові, а головне питання вони
 * не закривали взагалі: важливо не «які банки він приймає», а «чи є серед
 * них мій». Тепер відповідь видно кольором.
 *
 * Код, якого немає в реєстрі бота, показується як «код 545», а не як
 * назва банку: під нього картка не підбереться, скільки б їх не додати,
 * і виглядати він мусить саме прогалиною в довіднику.
 */

/** Поки довідник летить — щоб чипи не були порожніми. */
const FALLBACK: Record<string, string> = {
  '43': 'Monobank', '14': 'ПриватБанк', '64': 'ПУМБ', '48': 'А-Банк',
  '99': 'Ощадбанк', '380': 'Raiffeisen', '328': 'Sense', '319': 'OTP',
  '553': 'izibank', 'transfer': 'Global Transfer',
};

export function bankLabelFrom(code: string, known: Record<string, string>): string {
  return known[code]
    ?? FALLBACK[code]
    ?? (/^\d+$/.test(code) ? `код ${code}` : code);
}

/** Слаги банків, під які є активна картка. */
export function useMyBankSlugs(): Set<string> {
  return new Set(useMyBankBalances().keys());
}

/**
 * Скільки грошей лежить у кожному банку — сумою по активних картках.
 *
 * Потрібне не для показу, а для вибору: мерчант приймає кілька банків, і
 * питати матчинг треба про той, де грошей найбільше. Інакше блок бере
 * перший банк зі списку, натикається на 5 000 ₴ і каже «не вистачає»,
 * тоді як у сусідньому банку того ж списку лежить 21 000 ₴.
 */
export function useMyBankBalances(): Map<string, number> {
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const { data: cards } = useSWR<Card[]>(
    telegramId ? ['/cards', telegramId] : null,
    () => api.getCards(telegramId!),
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );

  return React.useMemo(() => {
    const out = new Map<string, number>();
    for (const c of cards ?? []) {
      if (c.status !== 'active') continue;
      const slug = String(c.bankName ?? '').toLowerCase();
      if (!slug) continue;
      out.set(slug, (out.get(slug) ?? 0) + (c.balance ?? 0));
    }
    return out;
  }, [cards]);
}

/**
 * Спільні дані для чипів: реєстр банків, довідник профілів і свої картки.
 * Усі три через SWR, тож між картками ордерів запит один.
 *
 * Потрібне лише як запасний шлях — коли бекенд не віддав готових назв.
 */
export function useBankChips() {
  const { data: bankList } = useSWR<Bank[]>('/banks', () => api.getBanks(), {
    revalidateOnFocus: false, revalidateIfStale: false, shouldRetryOnError: false,
  });
  const { profiles } = useBankProfiles();

  const telegramId = useAppStore(state => state.auth?.telegramId);
  const { data: cards } = useSWR<Card[]>(
    telegramId ? ['/cards', telegramId] : null,
    () => api.getCards(telegramId!),
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );

  const known = React.useMemo(
    () => Object.fromEntries((bankList ?? []).map(b => [b.code, b.name])),
    [bankList]
  );
  // Прямої мапи «код біржі → слаг картки» немає: /banks дає код і назву,
  // профілі — слаг і назву. Зшиваємо по назві, вона з одного джерела.
  const slugByName = React.useMemo(
    () => Object.fromEntries(profiles.map(p => [p.name.toLowerCase(), p.slug])),
    [profiles]
  );
  const mine = React.useMemo(
    () => new Set(
      (cards ?? [])
        .filter(c => c.status === 'active')
        .map(c => String(c.bankName ?? '').toLowerCase())
    ),
    [cards]
  );

  return React.useCallback(
    (codes: string[]) => codes.map(code => {
      const label = bankLabelFrom(code, known);
      const slug = slugByName[label.toLowerCase()] ?? '';
      return { code, label, slug, mine: Boolean(slug) && mine.has(slug) };
    }),
    [known, slugByName, mine]
  );
}

export function BankChips({
  codes, banks: fromApi, size = 'md',
}: {
  codes: string[];
  /**
   * Готові назви з бекенда. Мають перевагу над кодами: одному банку
   * відповідає кілька кодів («43» і «1» — Monobank), і мапа для цього
   * одна — там, де живе реєстр. Клієнт із самими кодами показував «код 1»
   * як невідомий банк.
   */
  banks?: BankRef[];
  size?: 'sm' | 'md';
}) {
  const resolve = useBankChips();
  const mineOf = useMyBankSlugs();

  if (!fromApi?.length && !codes?.length) return null;

  const banks = fromApi?.length
    ? fromApi.map(b => ({
        code: b.code,
        label: b.name,
        slug: b.slug,
        mine: b.known && mineOf.has(b.slug),
      }))
    : resolve(codes);
  const hasMine = banks.some(b => b.mine);

  return (
    <div className="flex flex-wrap gap-1">
      {banks.map(bank => (
        <span
          key={bank.code}
          title={bank.mine ? 'У тебе є картка цього банку' : undefined}
          className={cn(
            'rounded-md border font-bold',
            size === 'sm' ? 'px-1.5 py-0.5 text-[9px]' : 'px-2 py-0.5 text-[10px]',
            bank.mine
              ? 'bg-accent-500/10 border-accent-500/30 text-accent-400'
              : 'bg-slate-950 border-slate-800 text-slate-500'
          )}
        >
          {bank.label}
        </span>
      ))}

      {/* Мовчазний список банків, серед яких немає жодного твого, читається
          як «підходить» — хоча угоду за ним не взяти. */}
      {!hasMine && (
        <span className={cn(
          'rounded-md border border-orange-500/25 bg-orange-500/10 text-orange-400 font-bold',
          size === 'sm' ? 'px-1.5 py-0.5 text-[9px]' : 'px-2 py-0.5 text-[10px]'
        )}>
          немає твоєї картки
        </span>
      )}
    </div>
  );
}
