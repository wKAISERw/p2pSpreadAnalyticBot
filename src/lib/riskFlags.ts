/**
 * Розбір `risk_flag` — рядка, яким ризик-движок описує ордер.
 *
 * Формат склався на боці бота (`core/engine/risk_engine._join_flags`):
 *
 *     FLAG,FLAG,CODE:TYPE:людський текст із пробілами й комами
 *
 * Розділювач між флагами — кома; двокрапка розділяє код, тип і пояснення
 * всередині одного флага. Кома в самому поясненні замінюється на «;» ще на
 * беку, саме щоб не порвати рядок.
 *
 * Дашборд раніше різав його як `split(/[:,\s]+/)` — тобто ще й по пробілах.
 * Через це кожне слово пояснення ставало окремим бейджем: картка ордера
 * перетворювалась на стіну з тридцяти помаранчевих тегів «⚠ МЕРЧАНТ»,
 * «⚠ ВИМАГАЄ», «⚠ ОПЛАТУ». Найцінніше — сам текст висновку LLM — при цьому
 * ставало нечитабельним саме тому, що його показували «повністю».
 *
 * Тут рядок розбирається так само, як це робить `bot/formatters.py`:
 * коротка мітка окремо, пояснення окремо.
 */

export type RiskLevel = 'block' | 'suspicious' | 'unknown' | 'pending' | 'warn' | 'ok';

export interface RiskFlag {
  /** Короткий код для бейджа: LLM_SUSPICIOUS, BADREVIEWS, LOW_STATS… */
  code: string;
  /** Уточнення типу, якщо воно було: BLOCK:**MIDDLEMAN**:текст. */
  type: string;
  /** Людське пояснення. Може бути довгим — це висновок LLM. */
  detail: string;
  level: RiskLevel;
}

export interface RiskSummary {
  level: RiskLevel;
  /** Готова мітка вердикту — та сама, що бот пише в алерт. */
  verdict: string;
  flags: RiskFlag[];
  /** Усі пояснення, найдовше першим: це і є «думки» моделі. */
  explanations: string[];
  /** «Не змогли перевірити» — не звинувачення мерчанта, а чесна прогалина. */
  gaps: string[];
}

/** Ті самі мітки, що в bot/formatters.RISK_BADGES — щоб сайт і чат збігались. */
const VERDICTS: [string, string, RiskLevel][] = [
  ['BLOCK', '🚫 НЕ ТОРГУВАТИ', 'block'],
  ['REJECT', '🚫 НЕ ТОРГУВАТИ', 'block'],
  ['LLM_SUSPICIOUS', '⚡ З ОБЕРЕЖНІСТЮ', 'suspicious'],
  ['SUSPICIOUS', '⚡ З ОБЕРЕЖНІСТЮ', 'suspicious'],
  ['CONDITIONAL', '⚡ З ОБЕРЕЖНІСТЮ', 'suspicious'],
  ['RECHECKING', '🔄 AI перепровіряє', 'pending'],
  ['LLM_PENDING', '🔍 AI аналізує', 'pending'],
  ['PENDING', '🔍 AI аналізує', 'pending'],
  ['NEEDS_LLM', '🔍 AI аналізує', 'pending'],
  ['UNKNOWN', '❔ Не перевірено', 'unknown'],
];

/** Чому саме не вдалось перевірити — bot/formatters.UNKNOWN_REASONS. */
const UNKNOWN_REASONS: Record<string, string> = {
  NO_SESSION: 'немає сесії біржі',
  NO_AUTH: 'біржа не авторизує запит',
  UNAVAILABLE: 'біржа не відповідає',
  NOT_SUPPORTED: 'біржа не віддає відгуки',
  EMPTY: 'мерчант не вказав умов',
};

/** Код → рівень серйозності для кольору бейджа. */
function levelOf(code: string): RiskLevel {
  const upper = code.toUpperCase();
  if (upper.startsWith('BLOCK') || upper === 'REJECT') return 'block';
  if (upper.startsWith('UNKNOWN')) return 'unknown';
  if (upper.includes('PENDING') || upper.startsWith('NEEDS_LLM')) return 'pending';
  if (upper.includes('SUSPICIOUS')) return 'suspicious';
  return 'warn';
}

/**
 * Пояснення чи технічний хвіст?
 *
 * `S75`, `C42`, `COOLDOWN` — це службові значення, і показувати їх як
 * «думку моделі» означало б видати технічний шум за аналіз.
 */
function isHumanText(text: string): boolean {
  const trimmed = text.trim();
  if (trimmed.length < 12) return false;
  return /\s/.test(trimmed) && /[a-zA-Zа-яА-ЯіїєґІЇЄҐ]{4}/.test(trimmed);
}

export function parseRiskFlag(raw?: string | null): RiskSummary {
  const value = (raw ?? '').trim();
  if (!value || value.toUpperCase() === 'OK') {
    return { level: 'ok', verdict: '', flags: [], explanations: [], gaps: [] };
  }

  const flags: RiskFlag[] = [];
  const explanations: string[] = [];
  const gaps: string[] = [];

  for (const chunk of value.split(',')) {
    const piece = chunk.trim();
    if (!piece) continue;

    // Ділимо максимум на три частини: усе після другої двокрапки —
    // суцільний людський текст, і різати його далі не можна.
    const [head, second = '', ...rest] = piece.split(':');
    const code = head.trim();
    if (!code) continue;

    const tail = [second, ...rest].join(':').trim();
    const level = levelOf(code);

    if (level === 'unknown') {
      // UNKNOWN:REVIEWS:NO_SESSION → «відгуки — немає сесії біржі»
      const parts = piece.toUpperCase().split(':');
      const what = parts.includes('REVIEWS') ? 'відгуки' : 'умови угоди';
      const why = UNKNOWN_REASONS[parts[parts.length - 1]] ?? 'технічна причина';
      gaps.push(`${what} — ${why}`);
      flags.push({ code, type: second.trim(), detail: '', level });
      continue;
    }

    // Другий сегмент буває і типом ризику (MIDDLEMAN), і вже самим текстом.
    const typeIsCode = second.trim().length > 0 && !isHumanText(second);
    const detail = typeIsCode ? rest.join(':').trim() : tail;

    if (isHumanText(detail)) explanations.push(detail);
    flags.push({
      code,
      type: typeIsCode ? second.trim() : '',
      detail: isHumanText(detail) ? detail : '',
      level,
    });
  }

  const upper = value.toUpperCase();
  const matched = VERDICTS.find(([key]) => upper.includes(key));
  const level: RiskLevel = matched
    ? matched[2]
    : flags.some(f => f.level === 'block')
      ? 'block'
      : 'warn';

  return {
    level,
    verdict: matched ? matched[1] : '⚠️ Є зауваження',
    flags,
    // Найдовше пояснення першим: воно майже завжди і є висновком моделі.
    explanations: explanations.sort((a, b) => b.length - a.length),
    gaps,
  };
}

/** Класи бейджа під рівень. Один набір на всі місця, де показуються ризики. */
export const RISK_TONE: Record<RiskLevel, string> = {
  block: 'bg-red-500/10 text-red-400 border-red-500/25',
  suspicious: 'bg-orange-500/10 text-orange-400 border-orange-500/25',
  unknown: 'bg-slate-700/30 text-slate-300 border-slate-600/40',
  pending: 'bg-blue-500/10 text-blue-300 border-blue-500/25',
  warn: 'bg-amber-500/10 text-amber-400 border-amber-500/25',
  ok: 'bg-accent-500/10 text-accent-400 border-accent-500/25',
};
