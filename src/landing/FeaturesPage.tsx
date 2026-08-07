import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading, Rule } from './LandingLayout';
import { Reveal, GlowCard } from './motion';
import {
  BracketMetric, ConsoleFeed, Sparkline, VerdictRow, ScanStrip, FilterFunnel,
  LimitBars, SessionStatus, RiskSpectrum, SweepFrame,
  type LogLine, type ScanItem,
} from './blocks';

/**
 * Детальний розбір можливостей.
 *
 * Сторінка була дев'ятьма плитками в мозаїці: кожна показувала свій
 * модуль, але порядок читався як випадковий — з бенто не видно, чому
 * «Снайпер» стоїть після «Аналітики». Тепер модулі зібрані в три групи
 * за роллю: що шукає угоди, що їх перевіряє, і що ними керує. Плитки
 * всередині груп лишились тими самими.
 *
 * Іконки в шапках замінені на двобуквені монограми — дев'ять lucide-
 * іконок заради дев'яти заголовків не окупались, а моношрифт тут і так
 * несе всю «термінальну» мову сторінки.
 */

/* Порядок і склад — з ALL_EXCHANGES (core/engine/exchange_manager.py), сім штук. */
const EXCHANGES: ScanItem[] = [
  { name: 'Binance', code: 'BI' },
  { name: 'Bybit', code: 'BY' },
  { name: 'OKX', code: 'OK' },
  { name: 'MEXC', code: 'MX' },
  { name: 'Wallet', code: 'WL' },
  { name: 'BingX', code: 'BX' },
  { name: 'CryptoBot', code: 'CB' },
];

const SNIPER_LOG: LogLine[] = [
  { tone: 'muted', text: 'правило озброєне · спред ≥ 1.5% · обсяг ≥ 20 000 ₴' },
  { tone: 'muted', text: 'цикл: Bybit · OKX · Binance · MEXC · Wallet' },
  { tone: 'hit', text: 'OKX → Binance · 1.84% · 24 000 ₴' },
  { tone: 'muted', text: 'мерчант: 1840 угод · скарг немає · вердикт OK' },
  { tone: 'done', text: 'алерт надіслано в Telegram' },
];

/**
 * Приклади сигналів, а не профіль одного мерчанта.
 *
 * Це важливо для чесності блока: у макеті над цими рядками стояв
 * «ризик-скор 14/100» із підписом «профіль з вердиктом OK». За кодом так
 * не буває — W_REGEX = 0.28, тож сам лише regex-BLOCK дає ≥ 28 балів, а
 * докстрінг CompositeScorer прямо каже: «Regex BLOCK + будь-що ще →
 * composite >= 75». Профіль із рядком BLOCK не може мати 14.
 *
 * Замість вигаданого числа тут RiskSpectrum — він малює справжні пороги
 * to_verdict, а рядки нижче лишаються тим, чим і були: переліком типів
 * сигналів, які движок уміє бачити.
 */
const RISK_ROWS = [
  { verdict: 'BLOCK' as const, title: 'Оплата з чужих реквізитів', note: 'Правило на текст умов' },
  { verdict: 'WARN' as const, title: 'Чек перед відпуском', note: 'Тиск на апеляцію' },
  { verdict: 'WARN' as const, title: 'Липкі ліміти, 40 циклів', note: 'Поведінковий аналіз' },
  { verdict: 'OK' as const, title: '1840 угод, скарг немає', note: 'Відгуки прочитані' },
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

/* ────────────────────────── Каркас сторінки ────────────────────────── */

/**
 * Заголовок групи модулів.
 *
 * Номер + назва + волосяна лінія на решту ширини. Лінія тут не окраса:
 * без неї заголовок губиться між двома картками, бо за розміром він
 * менший за їхні шапки.
 */
const GroupHead: React.FC<{ num: string; title: string }> = ({ num, title }) => (
  <div className="flex items-center gap-4 mb-6">
    <span className="tag-mono text-[13px] font-bold text-accent-400 tabular-nums">{num}</span>
    <h2 className="text-xl font-bold text-white tracking-tight">{title}</h2>
    <span className="flex-1 h-px bg-slate-800/60" aria-hidden />
  </div>
);

/** Список пунктів модуля — спільний для всіх плиток. */
const Items: React.FC<{ items: string[][]; cols?: boolean }> = ({ items, cols }) => (
  <div className={cn('grid gap-x-8 gap-y-4', cols && 'sm:grid-cols-2')}>
    {items.map(([name, text]) => (
      <div key={name} className="flex gap-3">
        <span className="w-1.5 h-1.5 rounded-full bg-accent-400 shrink-0 mt-2" aria-hidden />
        <div className="min-w-0">
          <div className="text-sm font-bold text-slate-100 mb-0.5">{name}</div>
          <div className="text-[13px] text-slate-400 leading-relaxed">{text}</div>
        </div>
      </div>
    ))}
  </div>
);

/**
 * Час циклу дугою.
 *
 * Свідомо статична. У макеті дуга крутилась нескінченно, і поруч зі
 * сталим «0.6s» це читалось як живий вимір — якого тут немає й бути не
 * може: сторінка публічна й нічого не опитує. Правило по всьому лендингу
 * одне — ілюстративне не вдає живе.
 *
 * Весь вузол aria-hidden: те саме число словами стоїть в абзаці нижче,
 * тож для читалки це дубль.
 */
const CycleRing: React.FC<{ label: string }> = ({ label }) => (
  <div className="relative w-11 h-11 shrink-0" title="середній час циклу" aria-hidden>
    <svg viewBox="0 0 44 44" className="w-full h-full -rotate-90">
      <circle cx="22" cy="22" r="19" fill="none" stroke="rgb(30 41 59 / 0.8)" strokeWidth="3" />
      <circle
        cx="22"
        cy="22"
        r="19"
        fill="none"
        stroke="rgb(var(--accent-rgb))"
        strokeWidth="3"
        strokeLinecap="round"
        /* Довжина кола ≈ 119: третина дуги вистачає, щоб кільце читалось
           як заповнене частково, а не як обрізане. */
        strokeDasharray="34 85"
      />
    </svg>
    <span className="absolute inset-0 flex items-center justify-center tag-mono text-[9px] font-bold text-accent-400">
      {label}
    </span>
  </div>
);

/** Шапка плитки: монограма, назва, довільний бік, кількість пунктів. */
const TileHead: React.FC<{
  mark: string;
  title: string;
  count: number;
  aside?: React.ReactNode;
}> = ({ mark, title, count, aside }) => (
  <div className="flex items-center justify-between gap-4 mb-5">
    <div className="flex items-center gap-3 min-w-0">
      <span
        className="tag-mono w-10 h-10 rounded-xl bg-accent-500/15 border border-accent-500/30 flex items-center justify-center shrink-0 text-[11px] font-bold text-accent-400"
        aria-hidden
      >
        {mark}
      </span>
      <h3 className="text-[17px] font-bold text-white tracking-tight truncate">{title}</h3>
    </div>

    <div className="flex items-center gap-3 shrink-0">
      {aside}
      {/* Бейдж рахує пункти нижче — і має збігатися з ними. */}
      <span className="tag-mono text-[10px] px-2 py-1 rounded-lg bg-slate-950/80 text-slate-400 border border-slate-800">
        {count}
      </span>
    </div>
  </div>
);

/**
 * Обгортка плитки. Фактура задається зовні, щоб сусіди не збігались.
 *
 * Ховер один на всю сторінку — рамка. Ані чипи, ані панелі всередині
 * своїх станів не мають: коли підсвічується все, не підсвічується ніщо.
 */
const Tile: React.FC<{
  className?: string;
  texture?: string;
  children: React.ReactNode;
}> = ({ className, texture = 'bg-slate-900/50', children }) => (
  <GlowCard
    className={cn(
      'h-full min-w-0 border border-slate-800/80 rounded-3xl p-6 sm:p-7',
      'hover:border-accent-500/40 transition-colors',
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
          description="Сканер, ризик-движок, облік карток і аналітика — одна система, керована з Telegram або з вебдашборду. Три групи нижче: що шукає угоди, що їх перевіряє, і що ними керує."
        />

        {/*
          Кожне число звірене з кодом бота: ALL_EXCHANGES — сім
          майданчиків, _SCANNER_MODES — п'ять режимів, user_bank_limits —
          вісім полів ліміту, ваги CompositeScorer — шість сигналів.
        */}
        <SweepFrame className="mb-16 rounded-2xl">
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

        {/* ── 01 · Ядро сканування ─────────────────────────────────────── */}
        <GroupHead num="01" title="Ядро сканування" />

        <div className="grid gap-5 mb-16">
          <Reveal className="min-w-0">
            <Tile texture="surface-dots bg-slate-950/70">
              <TileHead
                mark="SC"
                title="Сканування"
                count={4}
                aside={<CycleRing label="0.6s" />}
              />

              <div className="grid lg:grid-cols-[1fr_1.1fr] gap-7 items-start">
                <div className="min-w-0">
                  <div className="tag-mono text-[10px] uppercase tracking-widest text-slate-400 mb-3">
                    один цикл — усі майданчики
                  </div>
                  <ScanStrip items={EXCHANGES} />
                  <p className="text-[13px] text-slate-400 leading-relaxed mt-4">
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

          <div className="grid lg:grid-cols-2 gap-5">
            {/*
              Схеми режимів сюди більше не йдуть: ті самі три діаграми
              стоять на «Як це працює», і дві сторінки показували одне й
              те саме. Тут достатньо назв — суть режимів розкрита там.
            */}
            <Reveal delay={60} className="min-w-0">
              <Tile texture="bg-slate-900/50">
                <TileHead mark="MD" title="Режими роботи" count={4} />

                <div className="flex gap-2 mb-5">
                  {['СПРЕД', 'ТЕЙКЕР', 'МЕЙКЕР'].map(m => (
                    <span
                      key={m}
                      className="tag-mono flex-1 text-center text-[11px] py-2.5 px-1.5 rounded-xl bg-slate-950/60 border border-slate-800/60 text-slate-400"
                    >
                      {m}
                    </span>
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

            <Reveal delay={120} className="min-w-0">
              <Tile texture="surface-scan bg-slate-950/80">
                <TileHead mark="SN" title="Снайпер" count={2} />

                <div className="mb-5">
                  <ConsoleFeed lines={SNIPER_LOG} caption="як спрацьовує правило" />
                </div>

                <Items
                  items={[
                    ['Пробиття тиші', 'Правило з порогами спреду й обсягу спрацює навіть під час паузи.'],
                    ['За біржами', 'Окреме правило для кожного майданчика й напрямку.'],
                  ]}
                />
              </Tile>
            </Reveal>
          </div>
        </div>

        {/* ── 02 · Захист і фільтрація ─────────────────────────────────── */}
        <GroupHead num="02" title="Захист і фільтрація" />

        <div className="grid lg:grid-cols-3 gap-5 mb-16">
          <Reveal className="lg:col-span-2 min-w-0">
            <Tile texture="surface-scan bg-slate-950/80">
              <TileHead mark="AF" title="Антифрод" count={4} />

              <div className="rounded-2xl bg-slate-950/60 border border-slate-800/70 p-4 sm:p-5 mb-5">
                <RiskSpectrum />
                <p className="text-[11px] text-slate-400 leading-snug mt-1">
                  Шкала движка: пороги 20 / 45 / 75 — ті самі, що в боті. Нижче — типи
                  сигналів, з яких складається бал.
                </p>
              </div>

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

          <Reveal delay={60} className="min-w-0">
            <Tile texture="surface-grid bg-slate-900/50">
              <TileHead mark="FL" title="Фільтри" count={4} />

              <FilterFunnel steps={FUNNEL} />
              <p className="text-[11px] text-slate-400 leading-snug mt-3 mb-6">
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
        </div>

        {/* ── 03 · Керування та облік ──────────────────────────────────── */}
        <GroupHead num="03" title="Керування та облік" />

        <div className="grid gap-5 mb-4">
          <div className="grid lg:grid-cols-3 gap-5">
            <Reveal className="min-w-0">
              <Tile texture="surface-dots bg-slate-950/70">
                <TileHead mark="CL" title="Картки й ліміти" count={4} />

                <LimitBars rows={LIMITS} />
                <p className="text-[11px] text-slate-400 leading-snug mt-3 mb-6">
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

            <Reveal delay={60} className="min-w-0">
              <Tile texture="bg-slate-900/50">
                <TileHead mark="AL" title="Сповіщення" count={4} />

                <div className="rounded-xl bg-slate-950/80 border border-slate-800 p-3.5">
                  <div className="flex items-center gap-2 mb-2.5">
                    <span className="tag-mono text-[10px] text-slate-400">18:42</span>
                    <span className="ml-auto tag-mono text-[10px] px-1.5 py-0.5 rounded bg-accent-500/15 text-accent-400">
                      ×3
                    </span>
                  </div>
                  <div className="text-lg font-black text-accent-400 tabular-nums leading-none mb-1.5">
                    +2.15%
                  </div>
                  <div className="tag-mono text-[10px] text-slate-400">OKX → Binance · 24 000 ₴</div>
                </div>
                <p className="text-[11px] text-slate-400 leading-snug mt-3 mb-6">
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

            <Reveal delay={120} className="min-w-0">
              <Tile texture="surface-grid bg-slate-900/50">
                <TileHead mark="AN" title="Аналітика" count={4} />

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
          </div>

          <Reveal delay={60} className="min-w-0">
            <Tile texture="surface-scan bg-slate-950/80">
              <TileHead mark="CT" title="Контроль" count={3} />

              <div className="grid lg:grid-cols-2 gap-7 items-start">
                <div className="min-w-0">
                  <SessionStatus rows={SESSIONS} />
                  <p className="text-[11px] text-slate-400 leading-snug mt-3">
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
        </div>
      </Section>

      <Rule />

      <Section className="pt-10 pb-16">
        <Reveal className="rounded-[2.5rem] border border-slate-800/80 bg-slate-900/80 backdrop-blur-2xl p-8 sm:p-12 hover:border-accent-500/40 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-6 shadow-2xl shadow-accent-500/5">
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

          {/*
            Пульс — окремий шар ПІД кнопкою, не на ній. Фон і текст самої
            кнопки статичні: анімувати opacity головного заклику означало б
            повторити те, що колись дало NO_LCP.
          */}
          <Link
            to="/login"
            className="relative isolate inline-flex items-center justify-center gap-2.5 px-8 py-4 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-all shadow-xl shadow-accent-500/25 shrink-0"
          >
            <span
              className="cta-pulse absolute inset-0 -z-10 rounded-2xl bg-accent-500/55"
              aria-hidden
            />
            Увійти в дашборд
            <ArrowRight className="w-5 h-5" />
          </Link>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
