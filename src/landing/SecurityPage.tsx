import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, ScanText, Activity, MessageSquareWarning, Ban, KeyRound, Lock, Server,
} from 'lucide-react';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import { Reveal, GlowCard } from './motion';

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
          eyebrow="Безпека"
          title="Спред без перевірки контрагента — половина картини"
          description="У P2P втрачають не на курсі, а на людині по той бік. Ризик-движок зводить чотири незалежні джерела в один бал і вердикт."
        />

        <div className="grid md:grid-cols-2 gap-4">
          {LAYERS.map((layer, i) => {
            const Icon = layer.icon;
            return (
              <Reveal key={layer.title} delay={(i % 2) * 90}>
              <GlowCard
                className="h-full bg-slate-900/40 border border-slate-800/60 rounded-3xl p-6 hover:border-accent-500/25 transition-colors"
              >
                <div className="w-11 h-11 rounded-2xl bg-accent-500/10 border border-accent-500/20 flex items-center justify-center mb-4">
                  <Icon className="w-5 h-5 text-accent-400" />
                </div>
                <h3 className="text-lg font-bold text-white mb-2">{layer.title}</h3>
                <p className="text-sm text-slate-400 leading-relaxed mb-4">{layer.text}</p>
                <div className="flex flex-wrap gap-1.5">
                  {layer.examples.map(ex => (
                    <span
                      key={ex}
                      className="px-2 py-1 rounded-lg bg-slate-950/70 border border-slate-800 text-[11px] text-slate-500"
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
          eyebrow="Вердикт"
          title="Три рівні, не «добре / погано»"
          description="Бал ризику складається з усіх джерел із вагами, які можна налаштувати. Різні люди готові до різного ризику, тож поріг теж твій."
        />

        <div className="grid sm:grid-cols-3 gap-4">
          <Verdict
            tone="ok"
            title="OK"
            text="Нічого підозрілого не знайдено. Алерт приходить як звичайно."
          />
          <Verdict
            tone="warn"
            title="WARNING"
            text="Є ознаки, вартні уваги. Зв'язку показано, але з поясненням, що саме насторожило."
          />
          <Verdict
            tone="block"
            title="BLOCK"
            text="Спрацювало жорстке правило — наприклад, оплата з чужих реквізитів. Такі зв'язки не показуються."
          />
        </div>
      </Section>

      <Section className="pt-0">
        <SectionHeading eyebrow="Дані" title="Де що лежить" />

        <div className="space-y-3">
          {DATA.map((item, i) => {
            const Icon = item.icon;
            return (
              <Reveal key={item.title} delay={i * 80}>
              <div
                className="flex gap-5 p-6 rounded-3xl bg-slate-900/40 border border-slate-800/60"
              >
                <Icon className="w-5 h-5 text-accent-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <h3 className="font-bold text-white mb-1.5">{item.title}</h3>
                  <p className="text-sm text-slate-400 leading-relaxed max-w-2xl">{item.text}</p>
                </div>
              </div>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Section className="pt-0">
        <Reveal className="rounded-[2rem] border border-orange-500/20 bg-orange-500/5 p-8">
          <h2 className="text-xl font-bold text-white mb-3">Чесно про межі</h2>
          <p className="text-sm text-slate-300 leading-relaxed max-w-2xl mb-4">
            Антифрод відсіює відомі схеми, а не всі можливі. Мерчант із чистою
            історією може повестися нечесно вперше саме з тобою; банк може
            заблокувати картку через обіг, до якого сканер не має стосунку.
            Інструмент зменшує ризик, але не прибирає його.
          </p>
          <Link
            to="/how-it-works"
            className="inline-flex items-center gap-2 text-sm font-bold text-accent-400 hover:text-accent-300 transition-colors"
          >
            Подивитись увесь конвеєр
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
