import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, Radar, ShieldCheck, CreditCard, SlidersHorizontal, Bell, LineChart,
  Layers, Crosshair, MonitorDot,
} from 'lucide-react';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import { Reveal, GlowCard } from './motion';

/**
 * Детальний розбір можливостей.
 *
 * Групи повторюють структуру самого продукту — те, що людина побачить у
 * меню бота й у дашборді. Так сторінка лишається правдою, а не набором
 * маркетингових формулювань, під якими нічого немає.
 */
const GROUPS = [
  {
    icon: Radar,
    title: 'Сканування',
    items: [
      ['Сім майданчиків', 'Binance, Bybit, OKX, MEXC, Telegram Wallet, BingX, CryptoBot — одночасно.'],
      ['Чистий спред', 'Різниця рахується після комісій біржі й мережевого переказу, а не «на око».'],
      ['Симуляція склянки', 'Ціна перевіряється на кількох обсягах — верхній ордер часто не тягне потрібну суму.'],
      ['Автоматичний cooldown', 'Біржа, що почала відмовляти, тимчасово виходить із циклу й не гальмує решту.'],
    ],
  },
  {
    icon: ShieldCheck,
    title: 'Антифрод',
    items: [
      ['Розбір умов', 'Правила на текст оголошення: треті особи, чек перед відпуском, зовнішні посилання.'],
      ['Поведінковий аналіз', 'Липкі ліміти, сплески швидкості, миттєве поповнення — ознаки бота.'],
      ['Аналіз відгуків', 'Мовна модель читає негатив і каже, що саме сталось, а не лише скільки мінусів.'],
      ['Чорні списки', 'Спільний і персональний, з режимами «блокувати», «ховати», «показувати».'],
    ],
  },
  {
    icon: SlidersHorizontal,
    title: 'Фільтри',
    items: [
      ['Пороги мерчанта', 'Кількість угод, рейтинг, % позитивних, вік акаунту, максимум офлайну.'],
      ['Окремо по біржах', 'Для кожного майданчика можна задати власні пороги — вони перебивають загальні.'],
      ['Банки', 'Загальний список або різні набори для купівлі й продажу.'],
      ['Капітал', 'Вручну або автоматично з реального балансу підключених карток.'],
    ],
  },
  {
    icon: Layers,
    title: 'Режими роботи',
    items: [
      ['Спред', 'Повна зв\'язка купівля → продаж між двома майданчиками.'],
      ['Тейкер Buy / Sell', 'Односторонній пошук із власними ціновими стратегіями й лімітами.'],
      ['Мейкер', 'Порада ціни для власного оголошення з урахуванням стінок ліквідності.'],
      ['Швидкість', 'FAST вимагає, щоб ордер тягнув усю суму; ANY достатньо мінімального ліміту.'],
    ],
  },
  {
    icon: CreditCard,
    title: 'Картки й ліміти',
    items: [
      ['Ліміти банків', 'Добові, місячні, на одну транзакцію, кількість операцій, пауза після ліміту.'],
      ['Власні ліміти картки', 'Окрема картка може мати свої значення — вони перебивають банківські.'],
      ['Monobank', 'Вебхук приносить транзакції одразу й сам звіряє їх із P2P-ордерами.'],
      ['Непрогріті картки', 'Окремий поріг для карток, які ще не набрали обіг.'],
    ],
  },
  {
    icon: Bell,
    title: 'Сповіщення',
    items: [
      ['Гнучкий вивід', 'Вибираєш, що показувати в алерті: умови, логіку AI, реквізити, вердикт.'],
      ['Групування', 'Однакові зв\'язки схлопуються в одне повідомлення замість потоку.'],
      ['Авто-затримка', 'При напливі спредів пауза між алертами росте за налаштованою драбинкою.'],
      ['Пауза', 'Алерти можна приглушити на годину, чотири або до ручного ввімкнення.'],
    ],
  },
  {
    icon: Crosshair,
    title: 'Снайпер',
    items: [
      ['Пробиття тиші', 'Правило з порогами спреду й обсягу спрацює навіть під час паузи.'],
      ['За біржами', 'Окреме правило для кожного майданчика й напрямку.'],
    ],
  },
  {
    icon: MonitorDot,
    title: 'Контроль',
    items: [
      ['Стан ядра', 'Старт і стоп сканера просто з дашборду.'],
      ['Сесії бірж', 'Видно, де сесія протухла — найчастіша причина, чому біржа замовкла.'],
      ['Незавершені угоди', 'Список того, що зараз під наглядом монітора ордерів.'],
      ['Черги', 'Скільки завдань чекає на розбір відгуків і мовну модель.'],
    ],
  },
  {
    icon: LineChart,
    title: 'Аналітика',
    items: [
      ['Прибуток по днях', 'Динаміка й накопичений результат за обраний період.'],
      ['Топ бірж і банків', 'Де реально йде обіг, а де лише здається.'],
      ['Теплова карта', 'Години й дні тижня, коли зв\'язок найбільше.'],
      ['Історія пропозицій', 'Що сканер знаходив, поки тебе не було.'],
    ],
  },
];

export default function FeaturesPage() {
  return (
    <LandingLayout>
      <Section className="pt-16 sm:pt-20">
        <SectionHeading
          eyebrow="Можливості"
          title="Що вміє Arbix Quantum"
          description="Повний перелік того, що є в системі. Керується і з Telegram-бота, і з вебдашборду — це одні й ті самі налаштування, просто два інтерфейси."
        />

        <div className="space-y-4">
          {GROUPS.map((group, gi) => {
            const Icon = group.icon;
            return (
              <Reveal key={group.title} delay={Math.min(gi, 3) * 60}>
              <GlowCard
                className="bg-slate-900/40 border border-slate-800/60 rounded-3xl p-6 sm:p-8"
              >
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-10 h-10 rounded-xl bg-accent-500/10 border border-accent-500/20 flex items-center justify-center shrink-0">
                    <Icon className="w-5 h-5 text-accent-400" />
                  </div>
                  <h2 className="text-xl font-bold text-white">{group.title}</h2>
                </div>

                <div className="grid sm:grid-cols-2 gap-x-8 gap-y-4">
                  {group.items.map(([name, text]) => (
                    <div key={name} className="flex gap-3">
                      <span className="w-1.5 h-1.5 rounded-full bg-accent-500/60 shrink-0 mt-2" />
                      <div className="min-w-0">
                        <div className="text-sm font-bold text-slate-200 mb-0.5">{name}</div>
                        <div className="text-sm text-slate-400 leading-relaxed">{text}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </GlowCard>
              </Reveal>
            );
          })}
        </div>
      </Section>

      <Section className="pt-0">
        <Reveal className="rounded-[2rem] border border-slate-800/60 bg-slate-900/40 p-8 sm:p-10 flex flex-col sm:flex-row sm:items-center justify-between gap-6">
          <div>
            <h2 className="text-2xl font-bold text-white mb-2">Готовий подивитись у дії?</h2>
            <p className="text-sm text-slate-400">
              Вхід через Telegram — усе вже налаштоване на стороні бота.
            </p>
          </div>
          <Link
            to="/login"
            className="inline-flex items-center gap-2 px-6 py-3.5 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-colors shrink-0"
          >
            Увійти
            <ArrowRight className="w-4 h-4" />
          </Link>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
