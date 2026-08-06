import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, ScanText, Activity, MessageSquareWarning, Ban, KeyRound, Lock, Server,
} from 'lucide-react';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import { Reveal, GlowCard } from './motion';
import { Surface, MonoTag, DataRain } from './surfaces';

/**
 * Антифрод і поводження з даними.
 *
 * Формулювання свідомо конкретні: «перевіряємо мерчанта» нічого не
 * означає, а «шукаємо вимогу чека перед відпуском» — означає. Там, де
 * гарантій немає, так і написано.
 */

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
          eyebrow="Anti-Scam & Ризик-Движок"
          title="Спред без перевірки контрагента — це пастка"
          description="У P2P арбітражі втрачають не на коливанні курсу, а на трикутниках, фінімоніторингу та скаргах. RiskEngine зводить 4 аналізатори в один hazard score від 0 до 100."
        />

        {/* Візуальний індикатор RiskEngine Scorer */}
        <Reveal delay={40} className="mb-12">
          <div className="bg-slate-900/80 border border-slate-800/80 backdrop-blur-2xl rounded-3xl p-6 sm:p-8 shadow-2xl">
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 mb-6 pb-6 border-b border-slate-800/80">
              <div>
                <h3 className="text-lg font-bold text-white mb-1">CompositeScorer: Оцінка рівня небезпеки (0–100)</h3>
                <p className="text-xs text-slate-400">Автоматичний аналіз умов угоди, клонів профілю та скарг у чаті</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="px-3 py-1 rounded-full text-xs font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">0-25: Безпечно</span>
                <span className="px-3 py-1 rounded-full text-xs font-bold bg-amber-500/10 text-amber-400 border border-amber-500/30">26-60: Увага</span>
                <span className="px-3 py-1 rounded-full text-xs font-bold bg-red-500/10 text-red-400 border border-red-500/30">61+: Блок</span>
              </div>
            </div>

            {/* Прогрес-бар ризиків */}
            <div className="space-y-4">
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs font-bold">
                  <span className="text-slate-300">Приклад: Перевірка мерчанта Binance</span>
                  <span className="text-emerald-400">Score 12 / 100 (Низький ризик)</span>
                </div>
                <div className="h-3 w-full bg-slate-950 rounded-full overflow-hidden p-0.5 border border-slate-800">
                  <div className="h-full bg-gradient-to-r from-emerald-500 via-amber-400 to-red-500 rounded-full transition-all" style={{ width: '12%' }} />
                </div>
              </div>
            </div>
          </div>
        </Reveal>

        <div className="grid md:grid-cols-2 gap-6">
          {LAYERS.map((layer, i) => {
            const Icon = layer.icon;
            return (
              <Reveal key={layer.title} delay={(i % 2) * 90}>
                <GlowCard
                  className="surface-dots h-full bg-slate-950/70 border border-slate-800/80 rounded-3xl p-7 hover:border-accent-500/40 transition-all shadow-xl"
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
                        className="px-2.5 py-1 rounded-lg bg-slate-950/80 border border-slate-800 text-xs font-semibold text-slate-300"
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
        <SectionHeading
          eyebrow="Вердикти"
          title="Три рівня реагування"
          description="Кожен вердикт супроводжується чітким поясненням причини в алерті Telegram та дашборді."
        />

        <div className="grid sm:grid-cols-3 gap-6">
          <Verdict
            tone="ok"
            title="OK (0-25)"
            text="Умови чисті, відгуки без фіксованого криміналу. Спред додається в дашборд і сповіщення."
          />
          <Verdict
            tone="warn"
            title="WARNING (26-60)"
            text="Виявлено нетипову поведінку або свіжі скарги. Алерт надходить із розширеним попередженням."
          />
          <Verdict
            tone="block"
            title="BLOCK (61-100)"
            text="Спрацював жорсткий фільтр (вимога фото картки, трикутник, казино). Оголошення відсікається."
          />
        </div>
      </Section>

      <Section className="pt-0">
        <SectionHeading eyebrow="Конфіденційність" title="Де зберігаються ваші ключі та дані" />

        <div className="space-y-4">
          {DATA.map((item, i) => {
            const Icon = item.icon;
            return (
              <Reveal key={item.title} delay={i * 80}>
                <div
                  className="flex gap-5 p-7 rounded-3xl bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl shadow-xl hover:border-slate-700/80 transition-all"
                >
                  <div className="w-10 h-10 rounded-2xl bg-slate-800 border border-slate-700/60 flex items-center justify-center shrink-0">
                    <Icon className="w-5 h-5 text-accent-400" />
                  </div>
                  <div className="min-w-0">
                    <h3 className="text-lg font-bold text-white mb-1.5">{item.title}</h3>
                    <p className="text-sm text-slate-400 leading-relaxed max-w-2xl">{item.text}</p>
                  </div>
                </div>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Section className="pt-0 pb-16">
        <Reveal className="rounded-[2.5rem] border border-amber-500/30 bg-amber-500/5 p-8 sm:p-10 shadow-2xl backdrop-blur-xl">
          <h2 className="text-xl font-bold text-white mb-3">Чесно про межі захисту</h2>
          <p className="text-sm text-slate-300 leading-relaxed max-w-3xl mb-5">
            RiskEngine суттєво мінімізує ризики, але не усуває людський фактор. Контрагент може вперше спробувати нечесні дії, або банк може надіслати запит на джерело коштів через обсяги. Сканер надає максимальну аналітику для вашої безпеки.
          </p>
          <Link
            to="/how-it-works"
            className="inline-flex items-center gap-2.5 px-6 py-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-accent-400 text-sm font-bold transition-all border border-slate-700"
          >
            Подивитись покроковий конвеєр
            <ArrowRight className="w-4 h-4" />
          </Link>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}

function Verdict({
  tone,
  title,
  text,
}: {
  tone: 'ok' | 'warn' | 'block';
  title: string;
  text: string;
}) {
  const style = {
    ok: 'border-accent-500/30 text-accent-400',
    warn: 'border-orange-500/30 text-orange-400',
    block: 'border-red-500/30 text-red-400',
  }[tone];

  return (
    <div className={`bg-slate-900/40 border rounded-2xl p-6 ${style.split(' ')[0]}`}>
      <div className={`text-sm font-black tracking-wider mb-2 ${style.split(' ')[1]}`}>{title}</div>
      <p className="text-sm text-slate-400 leading-relaxed">{text}</p>
    </div>
  );
}
