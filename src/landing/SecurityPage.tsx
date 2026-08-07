import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, ScanText, Activity, MessageSquareWarning, Ban, KeyRound, Lock, Server,
  Users, Brain, Percent, FileText,
} from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading, Rule } from './LandingLayout';
import { Reveal } from './motion';
import { MonoTag, DataRain, Card } from './surfaces';
import { BracketMetric, RiskSpectrum } from './blocks';
import ScanField from './ScanField';

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
  {
    key: 'W_REGEX',
    label: 'Розбір умов',
    weight: 28,
    icon: ScanText,
    text: 'Текст оголошення проти набору правил: треті особи, чек перед відпуском, зовнішні посилання, натяки на готівку й казино.',
  },
  {
    key: 'W_BEHAVIOR',
    label: 'Поведінка в часі',
    weight: 22,
    icon: Activity,
    text: 'Ліміти, що не рухаються десятки циклів, миттєве поповнення обсягу, аномальна швидкість — сигнатура бота.',
  },
  {
    key: 'W_LLM',
    label: 'Вердикт моделі',
    weight: 20,
    icon: Brain,
    text: 'Мовна модель читає негатив і формулює, що саме сталось.',
  },
  {
    key: 'W_REVIEWS_PCT',
    label: 'Частка негативу',
    weight: 12,
    icon: Percent,
    text: 'Кількісний сигнал: 40% негативу дає максимум за цим шаром.',
  },
  {
    key: 'W_IDENTITY',
    label: 'Клон профілю',
    weight: 10,
    icon: Users,
    text: 'Підтверджений двійник заблокованого мерчанта.',
  },
  {
    key: 'W_REVIEWS_TEXT',
    label: 'Тексти скарг',
    weight: 8,
    icon: FileText,
    text: 'Одна скарга про кидок важить більше за десять про повільність.',
  },
];

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

/**
 * Шлях даних — від пристрою до сховища.
 *
 * У референсі цей блок називався «client-side encryption → zero-knowledge
 * relay → decentralized vault». Нічого з цього в продукті немає, і
 * підписати чужу архітектуру під свою було б обманом. Тут — те, що
 * справді відбувається.
 */
const DATA_PATH = [
  {
    icon: Lock,
    tag: 'ВХІД',
    title: 'Особу підтверджує Telegram',
    text: 'id приходить підписаним, а не введеним у поле. Сесія прив\'язана до нього, і чужі дані під нею не відкриються.',
  },
  {
    icon: KeyRound,
    tag: 'КЛЮЧІ',
    title: 'Ключі бірж шифруються',
    text: 'API-ключі лежать зашифрованими. Бот при старті перевіряє ключ шифрування і не запускається, якщо не може прочитати збережене.',
  },
  {
    icon: Server,
    tag: 'СХОВИЩЕ',
    title: 'База — на твоєму сервері',
    text: 'Це не хмарний сервіс. Назовні йдуть лише запити до бірж і, якщо ти увімкнув розбір відгуків, знеособлені тексти в мовну модель.',
  },
];

export default function SecurityPage() {
  const top = WEIGHTS[0];
  const second = WEIGHTS[1];
  const rest = WEIGHTS.slice(2);

  return (
    <LandingLayout>
      {/*
        Скануючий промінь за шапкою сторінки. Живе тут, а не в макеті:
        це сторінка про перевірку, і фон, який безперервно щось «сканує»,
        тут доречний — на решті сторінок він був би просто шумом.

        Маска гасить його донизу, щоб текст секцій нижче лишався на
        спокійному фоні.
      */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[46rem] overflow-hidden"
        aria-hidden
      >
        <ScanField className="w-full h-full opacity-70" />
      </div>

      <Section className="pt-16 sm:pt-20 relative">
        <SectionHeading
          eyebrow="Ризик-движок"
          title="Спред без перевірки контрагента — це пастка"
          description="У P2P втрачають не на коливанні курсу, а на трикутниках, заморожених переказах і скаргах. Шість сигналів зводяться в один бал від 0 до 100, і від нього залежить, чи побачиш ти цю зв'язку взагалі."
        />

        <div className="mb-4">
          <MonoTag>composite_scorer</MonoTag>
        </div>

        {/*
          Бенто замість рівного списку: ваги неоднакові, тож і плитки
          неоднакові. Розбір умов важить 28% — він і займає найбільше
          місця. Ієрархія на екрані повторює ієрархію в коді.
        */}
        <div className="grid lg:grid-cols-3 gap-4 mb-5">
          <Reveal className="lg:col-span-2 min-w-0">
            {/*
              Ця картка була єдиною без ховера — на неї одну рамка не
              реагувала, і це читалось як несправність, а не як акцент.
              Вагу їй дає col-span-2, стовпчики й найбільший відсоток, а
              не окрема поверхня.
            */}
            <Card className="h-full">
              <div className="flex items-start justify-between gap-4 mb-6">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="w-10 h-10 rounded-xl bg-accent-500/15 border border-accent-500/30 flex items-center justify-center shrink-0">
                    <top.icon className="w-5 h-5 text-accent-400" />
                  </span>
                  <div className="min-w-0">
                    <div className="text-lg font-bold text-white truncate">{top.label}</div>
                    <div className="tag-mono text-[10px] text-slate-400">{top.key}</div>
                  </div>
                </div>
                <span className="tag-mono text-sm font-black text-accent-400 tabular-nums shrink-0">
                  {top.weight}%
                </span>
              </div>

              {/* Усі шість ваг стовпчиками — співвідношення видно одразу */}
              <div className="flex items-end gap-1.5 h-24 mb-5">
                {WEIGHTS.map((w, i) => (
                  <div key={w.key} className="flex-1 flex flex-col justify-end h-full group">
                    <div
                      className={cn(
                        'rounded-t transition-colors',
                        i === 0 ? 'bg-accent-400' : 'bg-accent-500/35 group-hover:bg-accent-500/60'
                      )}
                      style={{ height: `${(w.weight / 28) * 100}%` }}
                      title={`${w.label} — ${w.weight}%`}
                    />
                  </div>
                ))}
              </div>

              <p className="text-sm text-slate-400 leading-relaxed">{top.text}</p>
            </Card>
          </Reveal>

          <Reveal delay={80} className="min-w-0">
            <Card className="h-full flex flex-col">
              <div className="flex items-center gap-3 mb-4">
                <second.icon className="w-4 h-4 text-accent-400 shrink-0" />
                <span className="text-sm font-bold text-slate-200">{second.label}</span>
              </div>

              <div className="text-5xl font-black text-accent-400 tabular-nums leading-none mb-2">
                {second.weight}%
              </div>
              <div className="tag-mono text-[10px] uppercase tracking-widest text-slate-400 mb-4">
                {second.key}
              </div>

              <p className="text-[13px] text-slate-400 leading-relaxed mt-auto">{second.text}</p>
            </Card>
          </Reveal>
        </div>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-12">
          {rest.map((w, i) => {
            const Icon = w.icon;
            return (
              <Reveal key={w.key} delay={i * 60} className="min-w-0">
                {/* Менший радіус тут за розміром картки, а не за примхою:
                    вона вчетверо дрібніша за сусідні. */}
                <Card className="h-full rounded-2xl p-5">
                  <Icon className="w-5 h-5 text-accent-400 mb-3" />
                  <div className="flex items-baseline gap-2 mb-1.5">
                    <span className="text-2xl font-black text-white tabular-nums leading-none">
                      {w.weight}%
                    </span>
                  </div>
                  <div className="text-xs font-bold text-slate-300 mb-1.5">{w.label}</div>
                  <p className="text-[11px] text-slate-400 leading-snug">{w.text}</p>
                </Card>
              </Reveal>
            );
          })}
        </div>

        {/*
          Спектр однією шкалою. Чотири рівні картки приховували головне:
          смуги різної ширини, і OK — найвужча з них.
        */}
        <Reveal>
          <Card className="p-6 sm:p-8 mb-12">
            <div className="flex flex-wrap items-baseline justify-between gap-3 mb-6">
              <h3 className="text-lg font-bold text-white tracking-tight">Куди веде бал</h3>
              <span className="tag-mono text-[10px] uppercase tracking-widest text-slate-400">
                to_verdict
              </span>
            </div>

            <RiskSpectrum />

            <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-4 mt-6 pt-6 border-t border-slate-800/70">
              {[
                ['OK', 'Умови чисті, скарг по суті немає. Зв\'язка йде в алерт як є.'],
                ['WARN', 'Один слабкий сигнал. Показується з позначкою.'],
                ['SUSPICIOUS', 'Сигнали складаються: липкі ліміти плюс скарги. Алерт із розгорнутою причиною.'],
                ['BLOCK', 'Жорсткий сигнал — трикутник, вимога чека, клон. Не показується взагалі.'],
              ].map(([name, text]) => (
                <div key={name}>
                  <div className="tag-mono text-[11px] font-black text-slate-300 mb-1.5">{name}</div>
                  <p className="text-[13px] text-slate-400 leading-relaxed">{text}</p>
                </div>
              ))}
            </div>
          </Card>
        </Reveal>

        <div className="grid md:grid-cols-2 gap-6">
          {LAYERS.map((layer, i) => {
            const Icon = layer.icon;
            return (
              <Reveal key={layer.title} delay={(i % 2) * 90} className="min-w-0">
                <Card className="h-full p-7 shadow-xl">
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
                </Card>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Rule />

      <Section className="pt-0">
        <Reveal>
          <SectionHeading
            eyebrow="Конфіденційність"
            title="Де живуть ключі й дані"
            description="Коротка відповідь: у тебе. Довга — три кроки нижче."
          />
        </Reveal>

        {/*
          Сходинки зі зсувом: видно, що це шлях, а не три рівнозначні
          пункти. Кутові дужки замість рамки лишають блок відкритим.
        */}
        <div className="space-y-3">
          {DATA_PATH.map((item, i) => {
            const Icon = item.icon;
            return (
              <Reveal key={item.title} delay={i * 90}>
                <div
                  className="relative"
                  style={{ paddingLeft: `${i * 24}px` }}
                >
                  {/* З'єднувач до попереднього кроку */}
                  {i > 0 && (
                    <span
                      className="absolute w-px h-3 -top-3 bg-accent-500/40"
                      style={{ left: `${i * 24 - 12}px` }}
                      aria-hidden
                    />
                  )}

                  <Card className="relative flex items-start gap-4 p-5 sm:p-6 rounded-2xl">
                    <span className="absolute left-0 top-0 w-3 h-3 border-l border-t border-accent-500/40 rounded-tl-2xl" aria-hidden />
                    <span className="absolute right-0 bottom-0 w-3 h-3 border-r border-b border-accent-500/40 rounded-br-2xl" aria-hidden />

                    <span className="w-10 h-10 rounded-xl bg-accent-500/10 border border-accent-500/25 flex items-center justify-center shrink-0">
                      <Icon className="w-5 h-5 text-accent-400" />
                    </span>

                    <div className="min-w-0">
                      <div className="tag-mono text-[10px] tracking-widest text-accent-500 mb-1.5">
                        {item.tag}
                      </div>
                      <h3 className="text-base font-bold text-white mb-1.5">{item.title}</h3>
                      <p className="text-sm text-slate-400 leading-relaxed">{item.text}</p>
                    </div>
                  </Card>
                </div>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Rule />

      <Section className="pt-0 pb-16">
        <Reveal>
          <div className="relative rounded-[2.5rem] border border-amber-500/25 bg-amber-500/5 p-8 sm:p-10 overflow-hidden">
            <DataRain count={8} />
            <div className="relative">
              <h2 className="text-xl font-bold text-white mb-3">Чесно про межі захисту</h2>
              <p className="text-sm text-slate-300 leading-relaxed max-w-3xl mb-7">
                Перевірки ловлять відомі схеми, а не наміри. Мерчант із чистою історією
                може повестися нечесно вперше саме з тобою. Банк може попросити джерело
                коштів через обсяги. Оголошення може змінитись у мить між перевіркою і
                твоїм натисканням. Сканер зменшує ймовірність — не прибирає її.
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
