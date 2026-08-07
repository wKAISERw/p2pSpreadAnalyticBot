import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, Radar, ShieldCheck, CreditCard, SlidersHorizontal, Bell, LineChart,
  Layers, Crosshair, MonitorDot, Users, ReceiptText, Bot, CircleCheck, Send,
} from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import { Reveal, GlowCard } from './motion';
import {
  BracketMetric, ConsoleFeed, Sparkline, VerdictRow, ScanStrip, FilterFunnel,
  LimitBars, SessionStatus, StrategyDiagram, SweepFrame, type LogLine,
} from './blocks';

/**
 * Детальний розбір можливостей.
 *
 * Сторінка була сімома однаковими картками з маркованим списком у кожній
 * — структура однакова, тож і читалась як один довгий перелік, хоч модулі
 * різні по суті. Тепер кожна плитка показує саме те, чим її модуль
 * відрізняється: сканування — хвилю по біржах, антифрод — вердикти,
 * фільтри — лійку, картки — заповненість лімітів.
 *
 * Списки лишились, але вони більше не єдиний вміст блока.
 */

const EXCHANGES = ['Binance', 'Bybit', 'OKX', 'MEXC', 'Wallet', 'BingX', 'CryptoBot'];

const SNIPER_LOG: LogLine[] = [
  { tone: 'muted', text: 'правило озброєне · спред ≥ 1.5% · обсяг ≥ 20 000 ₴' },
  { tone: 'muted', text: 'цикл: Bybit · OKX · Binance · MEXC · Wallet' },
  { tone: 'hit', text: 'OKX → Binance · 1.84% · 24 000 ₴' },
  { tone: 'muted', text: 'мерчант: 1840 угод · скарг немає · вердикт OK' },
  { tone: 'muted', text: 'monobank: проходить під добовий ліміт' },
  { tone: 'done', text: 'алерт надіслано в Telegram' },
];

const RISK_ROWS = [
  { verdict: 'BLOCK' as const, icon: Users, title: 'Оплата з чужих реквізитів', note: 'Правило на текст умов' },
  { verdict: 'WARN' as const, icon: ReceiptText, title: 'Чек перед відпуском', note: 'Тиск на апеляцію' },
  { verdict: 'WARN' as const, icon: Bot, title: 'Липкі ліміти, 40 циклів', note: 'Поведінковий аналіз' },
  { verdict: 'OK' as const, icon: CircleCheck, title: '1840 угод, скарг немає', note: 'Відгуки прочитані' },
];

const FUNNEL = [
  { label: 'У склянках', value: 1240 },
  { label: 'Твої фільтри', value: 186 },
  { label: 'Ризик-движок', value: 14 },
  { label: 'В алерт', value: 3 },
];

const LIMITS = [
  { bank: 'monobank', used: 48000, limit: 150000 },
  { bank: 'ПриватБанк', used: 112000, limit: 150000 },
  { bank: 'ABank', used: 141000, limit: 150000 },
];

const SESSIONS = [
  { name: 'Binance', ok: true, note: 'жива' },
  { name: 'Bybit', ok: true, note: 'жива' },
  { name: 'OKX', ok: false, note: 'протухла' },
  { name: 'MEXC', ok: true, note: 'жива' },
];

/** Список пунктів модуля — спільний для всіх плиток. */
const Items: React.FC<{ items: string[][]; cols?: boolean }> = ({ items, cols }) => (
  <div className={cn('grid gap-x-8 gap-y-4', cols && 'sm:grid-cols-2')}>
    {items.map(([name, text]) => (
      <div key={name} className="flex gap-3 group">
        <span className="w-1.5 h-1.5 rounded-full bg-accent-400 shrink-0 mt-2 group-hover:scale-150 transition-transform" />
        <div className="min-w-0">
          <div className="text-sm font-bold text-slate-100 mb-0.5 group-hover:text-accent-400 transition-colors">
            {name}
          </div>
          <div className="text-[13px] text-slate-400 leading-relaxed">{text}</div>
        </div>
      </div>
    ))}
  </div>
);

/** Шапка плитки: іконка, назва, кількість пунктів. */
const TileHead: React.FC<{ icon: React.ElementType; title: string; count: number }> = ({
  icon: Icon,
  title,
  count,
}) => (
  <div className="flex items-center justify-between gap-4 mb-5">
    <div className="flex items-center gap-3 min-w-0">
      <div className="w-10 h-10 rounded-xl bg-accent-500/15 border border-accent-500/30 flex items-center justify-center shrink-0 shadow-lg shadow-accent-500/10">
        <Icon className="w-5 h-5 text-accent-400" />
      </div>
      <h2 className="text-lg font-bold text-white tracking-tight truncate">{title}</h2>
    </div>
    <span className="tag-mono text-[10px] px-2 py-1 rounded-lg bg-slate-950/80 text-slate-500 border border-slate-800 shrink-0">
      {count}
    </span>
  </div>
);

/** Обгортка плитки. Фактура задається зовні, щоб сусіди не збігались. */
const Tile: React.FC<{
  className?: string;
  texture?: string;
  children: React.ReactNode;
}> = ({ className, texture = 'bg-slate-900/50', children }) => (
  <GlowCard
    className={cn(
      'h-full min-w-0 border border-slate-800/80 rounded-3xl p-6 sm:p-7',
      'hover:border-accent-500/25 transition-colors',
      texture,
      className
    )}
  >
    {children}
  </GlowCard>
);

export default function FeaturesPage() {
  return (
    <LandingLayout>
      <Section className="pt-16 sm:pt-20">
        <SectionHeading
          eyebrow="Можливості"
          title="Що вміє Arbix Quantum"
          description="Сканер, ризик-движок, облік карток і аналітика — одна система, керована з Telegram або з вебдашборду. Нижче кожен модуль із тим, що він реально робить."
        />

        {/*
          Кожне число звірене з кодом бота: ALL_EXCHANGES — сім
          майданчиків, _SCANNER_MODES — п'ять режимів, user_bank_limits —
          вісім полів ліміту, ваги CompositeScorer — шість сигналів.
        */}
        <SweepFrame className="mb-12 rounded-2xl">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-6">
            {[
              { value: '7', label: 'майданчиків', hint: 'Binance, Bybit, OKX, MEXC, Wallet, BingX, CryptoBot' },
              { value: '6', label: 'сигналів ризику', hint: 'Умови, поведінка, LLM, негатив, тексти, клони' },
              { value: '5', label: 'режимів', hint: 'Спред, тейкер ×2, мейкер ×2' },
              { value: '8', label: 'лімітів на банк', hint: 'Добові, місячні, разові, кількість, кулдаун' },
            ].map((m, i) => (
              <Reveal key={m.label} delay={i * 40}>
                <BracketMetric {...m} />
              </Reveal>
            ))}
          </div>
        </SweepFrame>

        {/* ── Бенто ────────────────────────────────────────────────────── */}
        <div className="grid lg:grid-cols-3 gap-5">
          {/* Сканування — на всю ширину, бо з нього починається все інше */}
          <Reveal className="lg:col-span-3 min-w-0">
            <Tile texture="surface-dots bg-slate-950/70">
              <TileHead icon={Radar} title="Сканування" count={5} />

              <div className="grid lg:grid-cols-[1fr_1.1fr] gap-7 items-start">
                <div>
                  <div className="tag-mono text-[10px] uppercase tracking-widest text-slate-600 mb-3">
                    один цикл — усі майданчики
                  </div>
                  <ScanStrip items={EXCHANGES} />
                  <p className="text-[13px] text-slate-500 leading-relaxed mt-4">
                    Біржі опитуються паралельно, а не по черзі. Та, що почала відмовляти,
                    виходить у cooldown і не тримає решту — повний обхід займає 0.6–0.7 с.
                  </p>
                </div>

                <Items
                  items={[
                    ['Чистий спред', 'Різниця рахується після комісій біржі й мережевого переказу, а не «на око».'],
                    ['Симуляція склянки', 'Ціна перевіряється на кількох обсягах — верхній ордер часто не тягне суму.'],
                    ['Автоматичний cooldown', 'Біржа, що почала відмовляти, тимчасово виходить із циклу.'],
                    ['Швидкість циклу', '0.6–0.7 с у середньому, до 1.2 с коли майданчик гальмує.'],
                  ]}
                />
              </div>
            </Tile>
          </Reveal>

          {/* Антифрод — дві третини, з реальними вердиктами */}
          <Reveal delay={60} className="lg:col-span-2 min-w-0">
            <Tile texture="surface-scan bg-slate-950/80">
              <TileHead icon={ShieldCheck} title="Антифрод" count={4} />

              <div className="space-y-2 mb-6">
                {RISK_ROWS.map(r => (
                  <VerdictRow key={r.title} {...r} />
                ))}
              </div>

              <Items
                cols
                items={[
                  ['Розбір умов', 'Треті особи, чек перед відпуском, зовнішні посилання.'],
                  ['Поведінка', 'Липкі ліміти, сплески швидкості, миттєве поповнення.'],
                  ['Аналіз відгуків', 'Модель читає негатив і каже, що саме сталось.'],
                  ['Чорні списки', 'Спільний і персональний: блокувати, ховати, показувати.'],
                ]}
              />
            </Tile>
          </Reveal>

          {/* Фільтри — вузька колонка з лійкою */}
          <Reveal delay={120} className="min-w-0">
            <Tile texture="surface-grid bg-slate-900/50">
              <TileHead icon={SlidersHorizontal} title="Фільтри" count={4} />

              <FilterFunnel steps={FUNNEL} />
              <p className="text-[11px] text-slate-600 leading-snug mt-3 mb-6">
                Приклад одного циклу. Скільки саме відсіється — залежить від твоїх порогів.
              </p>

              <Items
                items={[
                  ['Пороги мерчанта', 'Угоди, рейтинг, % позитивних, вік, офлайн.'],
                  ['Окремо по біржах', 'Власні пороги перебивають загальні.'],
                  ['Банки', 'Різні набори для купівлі й продажу.'],
                  ['Капітал', 'Вручну або з балансу карток.'],
                ]}
              />
            </Tile>
          </Reveal>

          {/* Режими — зі схемами, які вже є на «як це працює» */}
          <Reveal delay={60} className="min-w-0">
            <Tile texture="bg-slate-900/50">
              <TileHead icon={Layers} title="Режими роботи" count={4} />

              <div className="grid grid-cols-3 gap-2 mb-5 -mx-1">
                {(['spread', 'taker', 'maker'] as const).map(k => (
                  <div key={k} className="rounded-xl bg-slate-950/60 border border-slate-800/60 p-1">
                    <StrategyDiagram kind={k} />
                  </div>
                ))}
              </div>

              <Items
                items={[
                  ['Спред', 'Повна зв\'язка купівля → продаж між майданчиками.'],
                  ['Тейкер Buy / Sell', 'Односторонній пошук зі своїми ціновими стратегіями.'],
                  ['Мейкер', 'Порада ціни з урахуванням стінок ліквідності.'],
                  ['Швидкість', 'FAST вимагає всю суму, ANY — мінімальний ліміт.'],
                ]}
              />
            </Tile>
          </Reveal>

          {/* Картки — зі смугами заповненості */}
          <Reveal delay={120} className="min-w-0">
            <Tile texture="surface-dots bg-slate-950/70">
              <TileHead icon={CreditCard} title="Картки й ліміти" count={4} />

              <div className="mb-2">
                <LimitBars rows={LIMITS} />
              </div>
              <p className="text-[11px] text-slate-600 leading-snug mb-6">
                Приклад добових лімітів. Обсяг зв'язки ріжеться під те, що лишилось.
              </p>

              <Items
                items={[
                  ['Ліміти банків', 'Добові, місячні, разові, кількість, пауза.'],
                  ['Власні ліміти картки', 'Перебивають банківські значення.'],
                  ['Monobank', 'Вебхук звіряє транзакції з P2P-ордерами.'],
                  ['Непрогріті картки', 'Окремий поріг, поки немає обігу.'],
                ]}
              />
            </Tile>
          </Reveal>

          {/* Сповіщення — з мініатюрою алерта */}
          <Reveal delay={180} className="min-w-0">
            <Tile texture="bg-slate-900/50">
              <TileHead icon={Bell} title="Сповіщення" count={4} />

              <div className="rounded-xl bg-slate-950/80 border border-slate-800 p-3 mb-2">
                <div className="flex items-center gap-2 mb-2.5">
                  <Send className="w-3.5 h-3.5 text-accent-400 shrink-0" />
                  <span className="tag-mono text-[10px] text-slate-500">18:42</span>
                  <span className="ml-auto tag-mono text-[10px] px-1.5 py-0.5 rounded bg-accent-500/15 text-accent-400">
                    ×3
                  </span>
                </div>
                <div className="text-lg font-black text-accent-400 tabular-nums leading-none mb-1">
                  +2.15%
                </div>
                <div className="tag-mono text-[10px] text-slate-500">OKX → Binance · 24 000 ₴</div>
              </div>
              <p className="text-[11px] text-slate-600 leading-snug mb-6">
                Позначка ×3 означає, що три однакові зв'язки схлопнулись в одне повідомлення.
              </p>

              <Items
                items={[
                  ['Гнучкий вивід', 'Що показувати: умови, логіку AI, реквізити, вердикт.'],
                  ['Групування', 'Однакові зв\'язки схлопуються в одне повідомлення.'],
                  ['Авто-затримка', 'При напливі пауза між алертами росте драбинкою.'],
                  ['Пауза', 'Приглушити на годину, чотири або до ручного вмикання.'],
                ]}
              />
            </Tile>
          </Reveal>

          {/* Контроль — на дві колонки, зі станом сесій */}
          <Reveal delay={60} className="lg:col-span-2 min-w-0">
            <Tile texture="surface-grid bg-slate-900/50">
              <TileHead icon={MonitorDot} title="Контроль" count={4} />

              <div className="grid lg:grid-cols-[1fr_1fr] gap-7 items-start">
                <div>
                  <SessionStatus rows={SESSIONS} />
                  <p className="text-[11px] text-slate-600 leading-snug mt-3">
                    Протухла сесія — найчастіша причина, чому біржа раптом замовкла.
                  </p>
                </div>

                <Items
                  items={[
                    ['Стан ядра', 'Старт і стоп сканера просто з дашборду.'],
                    ['Незавершені угоди', 'Що зараз під наглядом монітора ордерів.'],
                    ['Черги', 'Скільки завдань чекає на відгуки й мовну модель.'],
                  ]}
                />
              </div>
            </Tile>
          </Reveal>

          {/* Аналітика */}
          <Reveal delay={120} className="min-w-0">
            <Tile texture="surface-dots bg-slate-950/70">
              <TileHead icon={LineChart} title="Аналітика" count={4} />

              <div className="mb-5">
                <Sparkline title="Динаміка прибутку" />
              </div>

              <Items
                items={[
                  ['Прибуток по днях', 'Динаміка й накопичений результат за період.'],
                  ['Топ бірж і банків', 'Де реально йде обіг, а де лише здається.'],
                  ['Теплова карта', 'Години й дні, коли зв\'язок найбільше.'],
                  ['Історія пропозицій', 'Що сканер знаходив, поки тебе не було.'],
                ]}
              />
            </Tile>
          </Reveal>

          {/* Снайпер — на всю ширину: лог читається довгим рядком */}
          <Reveal delay={60} className="lg:col-span-3 min-w-0">
            <Tile texture="surface-scan bg-slate-950/80">
              <TileHead icon={Crosshair} title="Снайпер" count={2} />

              <div className="grid lg:grid-cols-[1.3fr_1fr] gap-7 items-start">
                <ConsoleFeed lines={SNIPER_LOG} caption="як спрацьовує правило" />

                <Items
                  items={[
                    ['Пробиття тиші', 'Правило з порогами спреду й обсягу спрацює навіть під час паузи.'],
                    ['За біржами', 'Окреме правило для кожного майданчика й напрямку.'],
                  ]}
                />
              </div>
            </Tile>
          </Reveal>
        </div>
      </Section>

      <Section className="pt-10 pb-16">
        <Reveal className="rounded-[2.5rem] border border-slate-800/80 bg-slate-900/80 backdrop-blur-2xl p-8 sm:p-12 flex flex-col sm:flex-row sm:items-center justify-between gap-6 shadow-2xl shadow-accent-500/5">
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-accent-500/10 border border-accent-500/20 text-accent-400 text-xs font-bold uppercase tracking-wider mb-3">
              Готовий до запуску
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-white mb-2 tracking-tight">
              Побач сканер у дії
            </h2>
            <p className="text-sm text-slate-400 max-w-md">
              Безпечний вхід через Telegram. Фільтри, картки й ліміти синхронізуються з ботом —
              налаштовувати вдруге не треба.
            </p>
          </div>
          <Link
            to="/login"
            className="inline-flex items-center justify-center gap-2.5 px-8 py-4 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-all shadow-xl shadow-accent-500/25 shrink-0"
          >
            Увійти в дашборд
            <ArrowRight className="w-5 h-5" />
          </Link>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
