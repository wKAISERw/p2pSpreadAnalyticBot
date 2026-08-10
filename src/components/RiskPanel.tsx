import React, { useState } from 'react';
import { AlertTriangle, ChevronDown, Brain, HelpCircle, FileText } from 'lucide-react';
import { cn } from '../lib/utils';
import { parseRiskFlag, RISK_TONE, RiskLevel } from '../lib/riskFlags';
import { AiVerdict } from '../types';

/**
 * Ризик ордера: вердикт, короткі бейджі й висновок моделі.
 *
 * До цього картка показувала `risk_flag` як є, розрізаний по пробілах, —
 * тобто тридцять помаранчевих тегів на кожне слово пояснення. Виглядало
 * це як «показуємо все», а насправді ховало єдине, що варте читання:
 * зв'язний текст висновку.
 *
 * Тепер порядок такий, як людина й вирішує: спершу вердикт, далі короткі
 * коди, і лише за запитом — повне пояснення. Мітки вердиктів збігаються з
 * тими, що бот пише в Telegram (`bot/formatters.RISK_BADGES`): одне й те
 * саме рішення не повинно називатись по-різному в двох місцях.
 */
export function RiskPanel({
  riskFlag, score = 0, compact = false, ai = null, children,
}: {
  riskFlag?: string | null;
  score?: number;
  /** Компактний режим: без розгорнутого тексту, лише вердикт і бейджі. */
  compact?: boolean;
  /**
   * Висновок моделі з таблиці вердиктів. Показується і тоді, коли ризиків
   * немає: «✅ Безпечно» з поясненням — теж відповідь, і саме її бракувало
   * ордерам із порожнім прапорцем.
   */
  ai?: AiVerdict | null;
  /**
   * Текст умов мерчанта. Стоїть між вижимкою і висновком моделі — саме в
   * тому порядку, в якому це читають: спершу вердикт, далі те, що написав
   * мерчант, і лише потім що про це думає модель.
   */
  children?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const risk = parseRiskFlag(riskFlag);

  const aiText = [ai?.reason, ai?.reviewsAnalysis].filter(Boolean) as string[];
  const hasAi = Boolean(ai && (aiText.length || ai.termsSummary));

  if (risk.level === 'ok' && score <= 0 && !hasAi) return null;

  const tone = RISK_TONE[risk.level];
  // Пояснення з таблиці вердиктів повніше за те, що влізло у прапорець.
  const explanations = aiText.length ? aiText : risk.explanations;
  const hasThoughts = explanations.length > 0;

  // Для «безпечних» прапорець порожній, і єдиний вердикт — від моделі.
  const verdictLabel = risk.verdict || ai?.recommendationLabel || '';
  const verdictTone = risk.verdict
    ? tone
    : ai?.recommendation === 'REJECT'
      ? RISK_TONE.block
      : ai?.recommendation === 'CONDITIONAL'
        ? RISK_TONE.suspicious
        : ai?.recommendation === 'APPROVE'
          ? RISK_TONE.ok
          : RISK_TONE.pending;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {verdictLabel && (
          <span className={cn(
            'px-2 py-0.5 rounded-md text-[11px] font-bold border',
            verdictTone
          )}>
            {verdictLabel}
          </span>
        )}

        {score > 0 && (
          <span className={cn(
            'flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase border',
            score >= 50
              ? 'bg-red-500/10 text-red-400 border-red-500/25'
              : 'bg-orange-500/10 text-orange-400 border-orange-500/25'
          )}>
            <AlertTriangle className="w-2.5 h-2.5" />
            скор {score}
          </span>
        )}

        {risk.flags.map((flag, i) => (
          <span
            key={`${flag.code}-${i}`}
            title={flag.detail || flag.type || undefined}
            className={cn(
              'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-tight border',
              RISK_TONE[flag.level as RiskLevel]
            )}
          >
            {flag.code}
            {flag.type && <span className="opacity-60">:{flag.type}</span>}
          </span>
        ))}
      </div>

      {/* «Не змогли перевірити» — не звинувачення мерчанта, і плутати це з
          ризиком не можна: рішення тут за людиною, а не за ботом. */}
      {risk.gaps.length > 0 && (
        <div className="flex items-start gap-1.5 text-[11px] text-slate-400">
          <HelpCircle className="w-3.5 h-3.5 shrink-0 mt-px text-slate-500" />
          <span>
            Не вдалось перевірити: {risk.gaps.join('; ')}. За рештою параметрів
            ордер підходить — далі на твій розсуд.
          </span>
        </div>
      )}

      {/* Вижимка умов. Мерчанти пишуть їх абзацами, і модель зводить до
          кількох рядків — саме це бот друкує під «📋 Умови». */}
      {ai?.termsSummary && !compact && (
        <div className="flex items-start gap-1.5 text-[11px] text-slate-400 leading-snug">
          <FileText className="w-3.5 h-3.5 shrink-0 mt-px text-slate-500" />
          <span>{ai.termsSummary}</span>
        </div>
      )}

      {children}

      {hasThoughts && !compact && (
        <div className="rounded-xl bg-slate-950/60 border border-slate-800/70 overflow-hidden">
          <button
            onClick={() => setOpen(v => !v)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-slate-800/30 transition-colors"
          >
            <Brain className="w-3.5 h-3.5 text-slate-500 shrink-0" />
            <span className="text-[11px] font-bold text-slate-400">
              Висновок AI
            </span>
            <ChevronDown className={cn(
              'w-3.5 h-3.5 text-slate-600 ml-auto transition-transform',
              open && 'rotate-180'
            )} />
          </button>

          {open && (
            <div className="px-3 pb-3 space-y-2">
              {explanations.map((text, i) => (
                <p key={i} className="text-[11px] text-slate-300 leading-relaxed">
                  {text}
                </p>
              ))}
            </div>
          )}
        </div>
      )}

      {hasThoughts && compact && (
        <p className="text-[11px] text-slate-400 leading-snug line-clamp-2">
          {explanations[0]}
        </p>
      )}
    </div>
  );
}
