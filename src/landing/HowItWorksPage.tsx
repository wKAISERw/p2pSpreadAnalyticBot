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
          description="П'ять етапів, кожен з яких відсіює зайве. Порядок не випадковий: важкі перевірки стоять після дешевих фільтрів, інакше цикл не встигав би за ринком."
        />

        <div className="space-y-3">
          {STEPS.map((step, i) => (
            <Step key={step.title} step={step} isLast={i === STEPS.length - 1} index={i} />
          ))}
        </div>
      </Section>

      <Section className="pt-0">
        <Reveal>
        <SectionHeading
          eyebrow="Режими"
          title="Три способи працювати"
          description="Режим перемикається під задачу — сканер по-різному відбирає ордери в кожному з них."
        />
        </Reveal>

        <div className="grid md:grid-cols-3 gap-4">
          {MODES.map((mode, i) => (
            <Reveal key={mode.name} delay={i * 90}>
              <GlowCard className="h-full bg-slate-900/40 border border-slate-800/60 rounded-2xl p-6">
                <h3 className="text-lg font-bold text-white mb-2">{mode.name}</h3>
                <p className="text-sm text-slate-400 leading-relaxed">{mode.text}</p>
              </GlowCard>
            </Reveal>
          ))}
        </div>
      </Section>

      <Section className="pt-0">
        <Reveal className="rounded-[2rem] border border-slate-800/60 bg-slate-900/40 p-8 sm:p-10">
          <h2 className="text-2xl font-bold text-white mb-3">Чого сканер не робить</h2>
          <ul className="space-y-2.5 text-sm text-slate-400 leading-relaxed max-w-2xl">
            <li>
              · <span className="text-slate-300">Не гарантує прибуток.</span> Спред живе секунди,
              і поки ти відкриваєш ордер, ціна може змінитись.
            </li>
            <li>
              · <span className="text-slate-300">Не приймає рішення за тебе.</span> Він показує
              зв'язку й ризик, натискаєш ти.
            </li>
            <li>
              · <span className="text-slate-300">Не прибирає ризик контрагента повністю.</span>
              {' '}Антифрод відсіює типові схеми, але чесність людини по той бік не гарантує ніхто.
            </li>
          </ul>

          <Link
            to="/security"
            className="inline-flex items-center gap-2 mt-6 text-sm font-bold text-accent-400 hover:text-accent-300 transition-colors"
          >
            Що саме перевіряє антифрод
            <ArrowRight className="w-4 h-4" />
          </Link>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
