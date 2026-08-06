import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, ScanText, Activity, MessageSquareWarning, Ban, KeyRound, Lock, Server,
  Users, Brain, Percent, FileText,
} from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import { Reveal, GlowCard } from './motion';
import { Surface, MonoTag, DataRain } from './surfaces';
import { BracketMetric } from './blocks';

/**
 * Антифрод і поводження з даними.
 *
 * Формулювання свідомо конкретні: «перевіряємо мерчанта» нічого не
 * означає, а «шукаємо вимогу чека перед відпуском» — означає. Там, де
 * гарантій немає, так і написано.
 */

/**
 * Ваги CompositeScorer — рівно ті, що стоять у core/engine/risk_engine.py.
 *
 * Показувати їх — свідоме рішення: продукт продає прозорість перевірки,
 * і найпростіший спосіб довести, що за словами щось є, — надрукувати
 * коефіцієнти. Вони завантажуються з bot_settings, тож це значення за
 * замовчуванням.
 */
const WEIGHTS = [
  { key: 'W_REGEX', label: 'Розбір умов', weight: 28, icon: ScanText },
  { key: 'W_BEHAVIOR', label: 'Поведінка в часі', weight: 22, icon: Activity },
  { key: 'W_LLM', label: 'Вердикт мовної моделі', weight: 20, icon: Brain },
  { key: 'W_REVIEWS_PCT', label: 'Частка негативу', weight: 12, icon: Percent },
  { key: 'W_IDENTITY', label: 'Клон профілю', weight: 10, icon: Users },
  { key: 'W_REVIEWS_TEXT', label: 'Тексти скарг', weight: 8, icon: FileText },
];

/**
 * Смуги вердикту.
 *
 * Пороги взяті з CompositeScorer.to_verdict — там їх чотири, а не три,
 * як було написано на цій сторінці раніше.
 */
const BANDS = [
  {
    name: 'OK',
    range: '0–19',
    tone: 'ok' as const,
    text: 'Умови чисті, поведінка звичайна, скарг по суті немає. Зв\'язка йде в алерт як є.',
  },
  {
    name: 'WARN',
    range: '20–44',
    tone: 'warn' as const,
    text: 'Один слабкий сигнал: трохи негативу або дрібна нетиповість. Показується з позначкою.',
  },
  {
    name: 'SUSPICIOUS',
    range: '45–74',
    tone: 'susp' as const,
    text: 'Сигнали складаються: липкі ліміти плюс скарги, або підозра на бота. Алерт іде з розгорнутою причиною.',
  },
  {
    name: 'BLOCK',
    range: '75–100',
    tone: 'block' as const,
    text: 'Спрацював жорсткий сигнал — трикутник, вимога чека, підтверджений клон. Оголошення не показується.',
  },
];

const BAND_STYLE = {
  ok: { text: 'text-accent-400', border: 'border-accent-500/30', bar: 'bg-accent-500' },
  warn: { text: 'text-yellow-400', border: 'border-yellow-500/30', bar: 'bg-yellow-500' },
  susp: { text: 'text-orange-400', border: 'border-orange-500/30', bar: 'bg-orange-500' },
  block: { text: 'text-red-400', border: 'border-red-500/30', bar: 'bg-red-500' },
};

const LAYERS = [
  {
    icon: ScanText,
    title: 'Розбір умов',
    text:
      'Текст оголошення проганяється через набір правил: вимога чека перед відпуском, оплата з чужих реквізитів, посилання назовні, натяки на казино чи обмін готівкою.',
    examples: ['третіх осіб', 'чек до відпуску', 'зовнішні посилання'],
  },
  {
    icon: Activity,
    title: 'Поведінка в часі',
    text:
      'Сканер пам\'ятає, як мерчант поводився раніше. Ліміти, що не рухаються десятки циклів, миттєве поповнення обсягу, аномальна швидкість угод — типова сигнатура бота.',
    examples: ['липкі ліміти', 'сплеск швидкості', 'API-поповнення'],
  },
  {
    icon: MessageSquareWarning,
    title: 'Відгуки',
    text:
      'Негативні відгуки збираються й читаються мовною моделлю: важливо не «скільки мінусів», а що саме сталось — заморозка, рефанд через банк чи просто повільна відповідь.',
    examples: ['% негативу', 'суть скарг', 'свіжість'],
  },
  {
    icon: Ban,
    title: 'Чорні списки',
    text:
      'Спільний список тих, хто вже відзначився, плюс твій власний. Для кожного можна обрати: блокувати, ховати чи показувати з позначкою.',
    examples: ['спільний', 'персональний', 'три режими'],
  },
];

const DATA = [
  {
    icon: KeyRound,
    title: 'Ключі бірж шифруються',
    text:
      'API-ключі зберігаються в зашифрованому вигляді. Бот при старті перевіряє ключ шифрування і не запускається, якщо не може прочитати збережене — краще не стартувати, ніж працювати з чужими креденшлами.',
  },
  {
    icon: Lock,
    title: 'Особу підтверджує Telegram',
    text:
      'Вхід у дашборд лише через підписаний Telegram: id приходить доведеним, а не введеним у поле. Персональні дані — фільтри, картки, баланси — доступні тільки власнику сесії.',
  },
  {
    icon: Server,
    title: 'Дані лишаються в тебе',
    text:
      'Це не хмарний сервіс: бот і база живуть на твоєму сервері. Назовні йдуть лише запити до бірж і, якщо ти увімкнув розбір відгуків, знеособлені тексти в мовну модель.',
  },
];

export default function SecurityPage() {
  return (
    <LandingLayout>
      <Section className="pt-16 sm:pt-20">
        <SectionHeading
          eyebrow="Ризик-движок"
          title="Спред без перевірки контрагента — це пастка"
          description="У P2P втрачають не на коливанні курсу, а на трикутниках, заморожених переказах і скаргах. Шість сигналів зводяться в один бал від 0 до 100, і від нього залежить, чи побачиш ти цю зв'язку взагалі."
        />

        {/*
          Ваги друкуються буквально. Це найсильніший аргумент сторінки:
          не «розумний алгоритм», а конкретні коефіцієнти, які видно.
        */}
        <Reveal className="mb-12">
          <Surface kind="scan" className="p-6 sm:p-8">
            <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
              <div>
                <div className="mb-3">
                  <MonoTag>composite_scorer</MonoTag>
                </div>
                <h3 className="text-xl font-bold text-white tracking-tight">
                  З чого складається бал
                </h3>
              </div>
              <span className="tag-mono text-[10px] uppercase tracking-widest text-slate-500">
                значення за замовчуванням
              </span>
            </div>

            {/* Одна смуга з шести часток — видно співвідношення, не читаючи цифр */}
            <div className="flex h-2 rounded-full overflow-hidden mb-7 gap-px">
              {WEIGHTS.map((w, i) => (
                <span
                  key={w.key}
                  className="bg-accent-500 transition-opacity"
                  style={{ width: `${w.weight}%`, opacity: 1 - i * 0.13 }}
                  aria-hidden
                />
              ))}
            </div>

            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-4">
              {WEIGHTS.map(w => {
                const Icon = w.icon;
                return (
                  <div key={w.key} className="flex items-center gap-3">
                    <Icon className="w-4 h-4 text-accent-400 shrink-0" />
                    <span className="text-sm text-slate-300 flex-1 min-w-0 truncate">
                      {w.label}
                    </span>
                    <span className="tag-mono text-sm font-bold text-white tabular-nums shrink-0">
                      {w.weight}%
                    </span>
                  </div>
                );
              })}
            </div>
          </Surface>
        </Reveal>

        {/* Смуги вердикту — горизонтальна шкала, а не три однакові картки */}
        <Reveal>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-px bg-slate-800/60 rounded-2xl overflow-hidden mb-12">
            {BANDS.map(band => {
              const s = BAND_STYLE[band.tone];
              return (
                <div key={band.name} className="bg-slate-950/80 p-5">
                  <div className={cn('h-1 w-10 rounded-full mb-4', s.bar)} />
                  <div className="flex items-baseline gap-2 mb-2">
                    <span className={cn('tag-mono text-sm font-black', s.text)}>{band.name}</span>
                    <span className="tag-mono text-[11px] text-slate-600 tabular-nums">
                      {band.range}
                    </span>
                  </div>
                  <p className="text-[13px] text-slate-400 leading-relaxed">{band.text}</p>
                </div>
              );
            })}
          </div>
        </Reveal>

        <div className="grid md:grid-cols-2 gap-6">
          {LAYERS.map((layer, i) => {
            const Icon = layer.icon;
            return (
              <Reveal key={layer.title} delay={(i % 2) * 90}>
                <GlowCard
                  className={cn(
                    'h-full border border-slate-800/80 rounded-3xl p-7 hover:border-accent-500/40 transition-all shadow-xl',
                    // Фактура чергується по діагоналі, щоб чотири однакові
                    // за структурою картки не читались як одна сітка.
                    i % 3 === 0 ? 'surface-dots bg-slate-950/70' : 'surface-grid bg-slate-900/50'
                  )}
                >
                  <div className="w-12 h-12 rounded-2xl bg-accent-500/15 border border-accent-500/30 flex items-center justify-center mb-5 shadow-lg shadow-accent-500/10">
                    <Icon className="w-6 h-6 text-accent-400" />
                  </div>
                  <h3 className="text-xl font-bold text-white mb-2">{layer.title}</h3>
                  <p className="text-sm text-slate-400 leading-relaxed mb-5">{layer.text}</p>
                  <div className="flex flex-wrap gap-2">
                    {layer.examples.map(ex => (
                      <span
                        key={ex}
                        className="tag-mono px-2.5 py-1 rounded-lg bg-slate-950/80 border border-slate-800 text-[11px] text-slate-400"
                      >
                        {ex}
                      </span>
                    ))}
                  </div>
                </GlowCard>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Section className="pt-0">
        <Reveal>
          <SectionHeading
            eyebrow="Конфіденційність"
            title="Де живуть ключі й дані"
            description="Коротка відповідь: у тебе. Довга — нижче."
          />
        </Reveal>

        {/*
          Три пункти навмисно різної ваги: перший — найважливіший, тож
          широкий і з фактурою. Раніше всі три були однаковими рядками,
          і читались як юридичний дрібний шрифт.
        */}
        <div className="grid md:grid-cols-2 gap-5">
          {DATA.map((item, i) => {
            const Icon = item.icon;
            const isWide = i === 0;
            return (
              <Reveal key={item.title} delay={i * 80} className={isWide ? 'md:col-span-2' : undefined}>
                <GlowCard
                  className={cn(
                    'h-full flex gap-5 p-6 sm:p-7 rounded-3xl border border-slate-800/80 hover:border-slate-700/80 transition-all',
                    isWide ? 'surface-scan bg-slate-950/80' : 'bg-slate-900/50'
                  )}
                >
                  <div className="w-11 h-11 rounded-2xl bg-slate-800/80 border border-slate-700/60 flex items-center justify-center shrink-0">
                    <Icon className="w-5 h-5 text-accent-400" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-lg font-bold text-white mb-1.5">{item.title}</h3>
                    <p className="text-sm text-slate-400 leading-relaxed">{item.text}</p>
                  </div>
                </GlowCard>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Section className="pt-0 pb-16">
        <Reveal>
          <div className="relative rounded-[2.5rem] border border-amber-500/25 bg-amber-500/5 p-8 sm:p-10 overflow-hidden">
            <DataRain count={8} />
            <div className="relative">
              <h2 className="text-xl font-bold text-white mb-3">Чесно про межі захисту</h2>
              <p className="text-sm text-slate-300 leading-relaxed max-w-3xl mb-6">
                {/*
                  Раніше тут було «суттєво мінімізує ризики» — фраза, що
                  нічого не означає й водночас звучить як обіцянка. Краще
                  назвати конкретні дірки: вони існують, і людина, яка
                  торгує, про них однаково дізнається.
                */}
                Перевірки ловлять відомі схеми, а не наміри. Мерчант із чистою історією
                може повестися нечесно вперше саме з тобою. Банк може попросити
                джерело коштів через обсяги. Оголошення може змінитись у мить між
                перевіркою і твоїм натисканням. Сканер зменшує ймовірність — не
                прибирає її.
              </p>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-7">
                <BracketMetric value="6" label="сигналів" hint="Зводяться в один бал" />
                <BracketMetric value="4" label="смуги" hint="OK, WARN, SUSPICIOUS, BLOCK" />
                <BracketMetric value="75" label="поріг блоку" hint="Вище — не показується" />
                <BracketMetric value="0" label="доступ до коштів" hint="Ордер створюєш ти" />
              </div>

              <Link
                to="/how-it-works"
                className="inline-flex items-center gap-2.5 px-6 py-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-accent-400 text-sm font-bold transition-all border border-slate-700"
              >
                Подивитись покроковий конвеєр
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
          </div>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
