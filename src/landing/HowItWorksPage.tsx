import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Search, Filter, ShieldCheck, Send, Wallet } from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import { Reveal, GlowCard, useReveal } from './motion';

/**
 * Конвеєр обробки — від опитування бірж до алерту.
 *
 * Порядок кроків тут не декоративний: він відповідає реальному циклу
 * сканера. Ризик-перевірка стоїть після пошуку зв'язки саме тому, що
 * перевіряти кожного мерчанта на біржі дорого — спершу відбираються
 * кандидати, і лише вони йдуть у ризик-движок.
 */
const STEPS = [
  {
    icon: Search,
    title: 'Опитування майданчиків',
    text:
      'Сканер знімає P2P-склянки з усіх увімкнених бірж. Біржа, яка почала відмовляти, автоматично йде в cooldown, щоб не тягнути за собою весь цикл.',
    detail: 'кожні кілька секунд',
  },
  {
    icon: Filter,
    title: 'Твої фільтри',
    text:
      'З усього обсягу лишається те, що підходить під твій капітал, банки, пороги мерчанта й режим роботи. Це відсіює переважну більшість ордерів ще до дорогих перевірок.',
    detail: 'капітал · банки · пороги',
  },
  {
    icon: ShieldCheck,
    title: 'Перевірка контрагента',
    text:
      'Кандидати йдуть у ризик-движок: розбір тексту умов, поведінка в часі, відгуки, чорні списки. Результат — бал ризику і вердикт.',
    detail: 'regex · поведінка · LLM',
  },
  {
    icon: Wallet,
    title: 'Звірка з картками',
    text:
      'Обсяг зіставляється з реальними лімітами твоїх банків: добовими, місячними й на одну транзакцію. Те, що не проходить, або зменшується, або відсікається.',
    detail: 'ліміти · залишки',
  },
  {
    icon: Send,
    title: 'Алерт',
    text:
      'Те, що пройшло всі етапи, приходить у Telegram з кнопками просто в застосунок біржі. Однакові зв\'язки схлопуються, щоб не заливати чат.',
    detail: 'Telegram · дашборд',
  },
];

const MODES = [
  {
    name: 'Спред',
    text: 'Класична зв\'язка: купив дешевше на одній біржі, продав дорожче на іншій. Сканер рахує чистий спред уже після комісій і мережевого переказу.',
  },
  {
    name: 'Тейкер',
    text: 'Полювання на чужі оголошення в один бік — тільки купівля або тільки продаж. Корисно, коли треба зайти або вийти за конкретною ціною.',
  },
  {
    name: 'Мейкер',
    text: 'Порада ціни для власного оголошення. Враховує стінки ліквідності: ставити перед великим обсягом конкурентів немає сенсу, ти просто не продаси.',
  },
];

/**
 * Крок конвеєра. З'єднувач до наступного кроку промальовується, коли крок
 * потрапляє у в'юпорт — так видно напрямок руху даних, а не просто список.
 */
const Step: React.FC<{
  step: (typeof STEPS)[number];
  isLast: boolean;
  index: number;
}> = ({ step, isLast, index }) => {
  const Icon = step.icon;
  const { ref, shown } = useReveal<HTMLDivElement>();

  return (
    <div
      ref={ref}
      className={cn('reveal', shown && 'reveal-in')}
      style={{ transitionDelay: `${index * 70}ms` }}
    >
      <GlowCard className="relative flex gap-5 p-5 sm:p-6 rounded-3xl bg-slate-900/40 border border-slate-800/60 hover:border-accent-500/25 transition-colors">
        <div className="flex flex-col items-center shrink-0">
          <div className="w-11 h-11 rounded-2xl bg-accent-500/10 border border-accent-500/20 flex items-center justify-center">
            <Icon className="w-5 h-5 text-accent-400" />
          </div>

          {!isLast && (
            <svg className="w-px flex-1 mt-3 overflow-visible" aria-hidden>
              <line
                x1="0.5" y1="0" x2="0.5" y2="100%"
                stroke="currentColor"
                className={cn(
                  'draw-line text-accent-500/45',
                  shown && 'draw-line-in'
                )}
                style={{ ['--len' as string]: '120' }}
                strokeWidth="1.5"
              />
            </svg>
          )}
        </div>

        <div className="min-w-0 pb-2">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
            <span className="text-[11px] font-black text-accent-500/70 tabular-nums">
              0{index + 1}
            </span>
            <h3 className="text-lg font-bold text-white">{step.title}</h3>
            <span className="text-[11px] font-mono text-slate-500">{step.detail}</span>
          </div>
          <p className="text-sm text-slate-400 leading-relaxed max-w-2xl">{step.text}</p>
        </div>
      </GlowCard>
    </div>
  );
};

export default function HowItWorksPage() {
  return (
    <LandingLayout>
      <Section className="pt-16 sm:pt-20">
        <SectionHeading
          eyebrow="Як це працює"
          title="Шлях від склянки до алерту"
          description="П'ять етапів асинхронного конвеєра, кожен з яких відсіює ризиковані угоди. Швидкі перевірки передують важким, зберігаючи затримку сканера під 1.2с."
        />

        <div className="grid lg:grid-cols-12 gap-8 items-start mb-16">
          {/* Ліва колонка — кроки */}
          <div className="lg:col-span-7 space-y-4">
            {STEPS.map((step, i) => (
              <Step key={step.title} step={step} isLast={i === STEPS.length - 1} index={i} />
            ))}
          </div>

          {/* Права колонка — Інтерактивне прев'ю алерта в Telegram */}
          <div className="lg:col-span-5 sticky top-24">
            <Reveal delay={120}>
              <GlowCard className="bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl rounded-3xl p-6 sm:p-7 shadow-xl">
                <div className="flex items-center justify-between gap-3 mb-5 pb-4 border-b border-slate-800/80">
                  <div className="flex items-center gap-2.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-accent-400 animate-pulse" />
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-200">Telegram Alert Preview</span>
                  </div>
                  <span className="text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700/60">Миттєве сповіщення</span>
                </div>

                {/* Картка алерта */}
                <div className="space-y-4 font-mono text-xs">
                  <div className="bg-slate-950/90 border border-slate-800 rounded-2xl p-4 space-y-3 shadow-inner">
                    <div className="flex items-center justify-between text-slate-200">
                      <span className="font-bold text-accent-400 text-sm">🚀 СПРЕД +2.15%</span>
                      <span className="text-[10px] text-slate-500">18:42:09</span>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-[11px] bg-slate-900/90 p-3 rounded-xl border border-slate-800/60">
                      <div>
                        <div className="text-slate-500 text-[9px] uppercase">Купівля (OKX)</div>
                        <div className="font-bold text-slate-200">41.15 ₴</div>
                        <div className="text-[10px] text-slate-400">Taker · PrivatBank</div>
                      </div>
                      <div>
                        <div className="text-slate-500 text-[9px] uppercase">Продаж (Binance)</div>
                        <div className="font-bold text-slate-200">42.03 ₴</div>
                        <div className="text-[10px] text-slate-400">Maker · Monobank</div>
                      </div>
                    </div>

                    <div className="space-y-1.5 text-[11px]">
                      <div className="flex items-center justify-between">
                        <span className="text-slate-400">Чистий прибуток:</span>
                        <span className="text-accent-400 font-bold">+880.50 ₴ (за 1 коло)</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-slate-400">Вердикт RiskEngine:</span>
                        <span className="px-2 py-0.5 rounded bg-accent-500/20 text-accent-400 font-bold text-[10px]">БЕЗПЕЧНО (Score 8/100)</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-slate-400">Контрагент:</span>
                        <span className="text-slate-300">1840 угоди (99.4%)</span>
                      </div>
                    </div>

                    <div className="pt-2 flex gap-2">
                      <button className="flex-1 py-2 bg-accent-500 hover:bg-accent-400 text-slate-950 font-sans font-bold text-xs rounded-xl transition-all text-center shadow-md">
                        Відкрити на OKX
                      </button>
                      <button className="flex-1 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 font-sans font-bold text-xs rounded-xl transition-colors text-center border border-slate-700/60">
                        Спліт карти
                      </button>
                    </div>
                  </div>
                </div>

                <p className="text-xs text-slate-400 mt-4 leading-relaxed font-sans">
                  Алерт містить прямі deeplinks на відкриття угоди, вердикт штучного інтелекту щодо умов та розподіл суми по картах.
                </p>
              </GlowCard>
            </Reveal>
          </div>
        </div>
      </Section>

      <Section className="pt-0">
        <Reveal>
          <SectionHeading
            eyebrow="Стратегії"
            title="Три способи торгівлі"
            description="Режим перемикається під поточні задачі — сканер по-різному розраховує ліквідність у кожному з них."
          />
        </Reveal>

        <div className="grid md:grid-cols-3 gap-6">
          {MODES.map((mode, i) => (
            <Reveal key={mode.name} delay={i * 90}>
              <GlowCard className="h-full bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl rounded-3xl p-7 hover:border-accent-500/40 transition-all shadow-xl">
                <div className="inline-flex items-center justify-center w-10 h-10 rounded-xl bg-accent-500/10 border border-accent-500/20 text-accent-400 font-bold mb-4">
                  0{i + 1}
                </div>
                <h3 className="text-xl font-bold text-white mb-2">{mode.name}</h3>
                <p className="text-sm text-slate-400 leading-relaxed">{mode.text}</p>
              </GlowCard>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section className="pt-0 pb-16">
        <Reveal>
          <GlowCard className="rounded-[2.5rem] border border-slate-800/80 bg-slate-900/60 backdrop-blur-xl p-8 sm:p-12 shadow-xl hover:border-slate-700/80 transition-all">
            <h2 className="text-2xl font-bold text-white mb-4">Чого сканер НЕ робить</h2>
            <ul className="space-y-3 text-sm text-slate-300 leading-relaxed max-w-2xl">
              <li className="flex gap-3">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0 mt-2" />
                <span><strong className="text-white">Не гарантує прибуток.</strong> Спред існує секунди. Поки ти відкриваєш ордер, ціна може змінитися на біржі.</span>
              </li>
              <li className="flex gap-3">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0 mt-2" />
                <span><strong className="text-white">Не здійснює авто-списання коштів з ваших карток.</strong> Ви самі контролюєте переказ і натискання кнопок підтвердження.</span>
              </li>
              <li className="flex gap-3">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0 mt-2" />
                <span><strong className="text-white">Не гарантує 100% чесність контрагента.</strong> RiskEngine відсіює 99% шахрайських схем, але остаточне рішення залишається за вами.</span>
              </li>
            </ul>

            <Link
              to="/security"
              className="inline-flex items-center gap-2 mt-8 px-6 py-3 rounded-xl bg-slate-800/80 hover:bg-slate-700/80 text-accent-400 text-sm font-bold transition-all border border-slate-700/60"
            >
              Детальніше про алгоритми Anti-Scam
              <ArrowRight className="w-4 h-4" />
            </Link>
          </GlowCard>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
