import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, Radar, ShieldCheck, CreditCard, SlidersHorizontal, Bell, LineChart,
  Layers, Crosshair, MonitorDot,
} from 'lucide-react';
import { cn } from '../lib/utils';
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
          eyebrow="Можливості та Арсенал"
          title="Що вміє Arbix Quantum"
          description="Повний перелік інструментів сканування, Anti-Scam перевірки, авто-репрайсера та моніторингу лімітів карт. Керується як з Telegram-бота, так і з сучасного вебдашборду."
        />

        {/* Швидкі метрики системи */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-10">
          {[
            { label: 'Майданчиків', val: '7+', desc: 'Binance, Bybit, OKX, MEXC, Wallet, BingX, CryptoBot' },
            { label: 'Швидкість скану', 'val': '< 1.2s', desc: 'Асинхронний паралельний потік' },
            { label: 'Захист Anti-Scam', val: '100%', desc: 'Regex + Behavioral + LLM Scorer' },
            { label: 'Авто-Репрайсер', val: 'ТОП-1', desc: 'Утримання позиції оголошення' },
          ].map((m, i) => (
            <Reveal key={m.label} delay={i * 40}>
              <div className="bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl rounded-2xl p-4 sm:p-5 hover:border-accent-500/30 transition-all">
                <div className="text-2xl sm:text-3xl font-black text-white tracking-tight mb-1">
                  <span className="text-accent-400">{m.val}</span>
                </div>
                <div className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-1">{m.label}</div>
                <div className="text-[11px] text-slate-500 leading-tight">{m.desc}</div>
              </div>
            </Reveal>
          ))}
        </div>

        <div className="grid md:grid-cols-2 gap-5 auto-rows-min">
          {GROUPS.map((group, gi) => {
            const Icon = group.icon;
              // Перші дві групи — сканування й антифрод — це суть
              // продукту, тож вони займають усю ширину. Решта парами.
              const isWide = gi < 2;

              return (
              <Reveal
                key={group.title}
                delay={Math.min(gi, 4) * 50}
                className={isWide ? 'md:col-span-2' : undefined}
              >
                <GlowCard
                  className={cn(
                    'h-full bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl rounded-3xl p-6 sm:p-8 hover:border-slate-700/80 transition-all shadow-xl shadow-slate-950/50',
                    isWide && 'md:bg-slate-900/75'
                  )}
                >
                  <div className="flex items-center justify-between gap-4 mb-6 pb-4 border-b border-slate-800/80">
                    <div className="flex items-center gap-3">
                      <div className="w-11 h-11 rounded-2xl bg-accent-500/15 border border-accent-500/30 flex items-center justify-center shrink-0 shadow-lg shadow-accent-500/10">
                        <Icon className="w-5 h-5 text-accent-400" />
                      </div>
                      <div>
                        <h2 className="text-xl font-bold text-white tracking-tight">{group.title}</h2>
                        <span className="text-xs text-slate-400">Модуль ядра Arbix Quantum</span>
                      </div>
                    </div>
                    <span className="hidden sm:inline-flex px-3 py-1 rounded-full text-[11px] font-semibold bg-slate-800/80 text-accent-400 border border-accent-500/20">
                      {group.items.length} параметри
                    </span>
                  </div>

                  <div className="grid sm:grid-cols-2 gap-x-8 gap-y-5">
                    {group.items.map(([name, text]) => (
                      <div key={name} className="flex gap-3 group">
                        <span className="w-2 h-2 rounded-full bg-accent-400 shrink-0 mt-2 group-hover:scale-125 transition-transform" />
                        <div className="min-w-0">
                          <div className="text-sm font-bold text-slate-100 mb-1 group-hover:text-accent-400 transition-colors">
                            {name}
                          </div>
                          <div className="text-sm text-slate-400 leading-relaxed">
                            {text}
                          </div>
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

      <Section className="pt-8 pb-16">
        <Reveal className="rounded-[2.5rem] border border-slate-800/80 bg-slate-900/80 backdrop-blur-2xl p-8 sm:p-12 flex flex-col sm:flex-row sm:items-center justify-between gap-6 shadow-2xl shadow-accent-500/5">
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-500/10 border border-accent-500/20 text-accent-400 text-xs font-bold uppercase tracking-wider mb-3">
              Готовий до запуску
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2 tracking-tight">Побач сканер у дії</h2>
            <p className="text-sm text-slate-400 max-w-md">
              Безпечний вхід через Telegram. Фількористувацькі налаштування, картки й ліміти синхронізуються автоматично.
            </p>
          </div>
          <Link
            to="/login"
            className="inline-flex items-center justify-center gap-2.5 px-8 py-4 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-all shadow-xl shadow-accent-500/25 shrink-0"
          >
            Увійти у дашборд
            <ArrowRight className="w-5 h-5" />
          </Link>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
