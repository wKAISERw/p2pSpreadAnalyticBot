import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, ShieldCheck, Radar, CreditCard, Bell, Brain, LineChart,
  Layers, Bot, Clock, Users, ReceiptText, CircleCheck,
} from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading, Rule } from './LandingLayout';
import SpreadVisual from './SpreadVisual';
import { Reveal, GlowCard } from './motion';
import { Surface, DataRain, MonoTag } from './surfaces';
import TrustStrip from './TrustStrip';
import Faq from './Faq';
import ScanField from './ScanField';
import { VerdictRow, BracketMetric, SweepFrame, BrandMark } from './blocks';

const EXCHANGES = ['Binance', 'Bybit', 'OKX', 'MEXC', 'Wallet', 'BingX', 'CryptoBot'];

/**
 * Приклад того, що ризик-движок бачить в одному мерчанті.
 *
 * Формулювання взяті з реальних правил: трикутник, тиск чеком, липкі
 * ліміти. Вердикти — ті самі, що пише бот.
 */
const RISK_SIGNALS = [
  { verdict: 'BLOCK' as const, icon: Users, title: 'Оплата з чужих реквізитів', note: 'Ознака трикутника' },
  { verdict: 'WARN' as const, icon: ReceiptText, title: 'Просить чек перед відпуском', note: 'Тиск на апеляцію' },
  { verdict: 'WARN' as const, icon: Bot, title: 'Ліміти не змінюються 40 циклів', note: 'Схоже на бота' },
  { verdict: 'OK' as const, icon: CircleCheck, title: '1840 угод, 99.2% завершення', note: 'Скарг немає' },
];

const PILLARS = [
  {
    icon: Radar,
    // Дрібні візуалізації під кожну опору: голий текст у трьох колонках
    // читається як список, а не як три різні можливості.
    visual: 'exchanges' as const,
    title: 'Сканує сім майданчиків',
    text:
      'Одночасно тримає в полі зору P2P-склянки всіх підключених бірж і шукає зв\'язки, де різниця курсів перекриває комісії й мережевий переказ.',
  },
  {
    icon: ShieldCheck,
    visual: 'risk' as const,
    title: 'Перевіряє контрагента',
    text:
      'Кожен мерчант проходить перевірку до того, як ти побачиш алерт: текст умов, поведінка, відгуки, чорні списки. Спред без цього — половина картини.',
  },
  {
    icon: CreditCard,
    visual: 'limits' as const,
    title: 'Пам\'ятає про ліміти карток',
    text:
      'Знає добові й місячні ліміти твоїх банків і не пропонує обсяг, який ти фізично не проведеш. Monobank підключається вебхуком і сам звіряє надходження.',
  },
];

const FEATURES = [
  { icon: Brain, title: 'Ризик-движок', text: 'Регексні правила, поведінковий аналіз і LLM-розбір відгуків зводяться в один бал ризику.' },
  { icon: Bot, title: 'Виявлення ботів', text: 'Сплески швидкості, липкі ліміти, миттєве поповнення — ознаки автоматичного контрагента.' },
  { icon: Layers, title: 'Тейкер і мейкер', text: 'Полювання на чужі оголошення або порада ціни для власного, з урахуванням стінок ліквідності.' },
  { icon: Bell, title: 'Алерти в Telegram', text: 'Сигнал приходить у бот із кнопками, що ведуть одразу в застосунок біржі.' },
  { icon: LineChart, title: 'Аналітика', text: 'Прибуток по днях, топ бірж і банків, теплова карта годин, будні проти вихідних.' },
  { icon: Clock, title: 'Історія пропозицій', text: 'Кожен знайдений спред зберігається — видно, що сканер знаходив, поки тебе не було.' },
];

export default function LandingPage() {
  return (
    <LandingLayout>
      {/*
        Сітка вузлів за героєм. Третій малюнок на третій сторінці —
        промінь для безпеки, потік для конвеєра, дихаюча сітка тут.
        Однаковий фон на всіх зводив би нанівець сам сенс його мати.
      */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[44rem] overflow-hidden"
        style={{
          maskImage: 'linear-gradient(to bottom, black 45%, transparent)',
          WebkitMaskImage: 'linear-gradient(to bottom, black 45%, transparent)',
        }}
        aria-hidden
      >
        <ScanField variant="mesh" className="w-full h-full opacity-60 mix-blend-screen" />
      </div>

      {/* ─── Герой ─────────────────────────────────────────────────────── */}
      <div className="relative overflow-hidden">
        {/*
          Локальних плям тут більше немає — фон малює AuroraField на рівні
          каркаса. Три великі blur-шари поверх нього були б подвійною
          роботою для GPU за той самий візуальний результат.
        */}
        <Section className="enter-rise relative pt-14 sm:pt-20 pb-10">
          <div className="hero-drift grid lg:grid-cols-2 gap-10 lg:gap-16 items-center">
            <div className="animate-rise">
              {/*
                Знак і ярлик в одному рядку. Знак узятий зі сторінки
                входу — там кутові дужки виявились найвдалішою деталлю
                всього оформлення, тож їм місце й на першому екрані, а не
                тільки за формою логіна.
              */}
              <div className="flex items-center gap-4 mb-6">
                <BrandMark />
                <div className="tag-mono inline-flex items-center gap-2 text-[10px] sm:text-[11px] uppercase tracking-[0.18em] text-accent-400">
                  <span className="w-1.5 h-1.5 bg-accent-500 shrink-0" />
                  P2P-арбітраж · Україна
                </div>
              </div>

              <h1 className="text-[2.6rem] sm:text-5xl lg:text-6xl font-bold text-white tracking-tight leading-[1.03] mb-5">
                Спред видно всім.
                <br />
                <span className="text-accent-400">Ризик — ні.</span>
              </h1>

              <p className="text-base sm:text-lg text-slate-400 leading-relaxed mb-8 max-w-lg">
                Arbix Quantum знаходить різницю курсів між P2P-майданчиками
                й одразу перевіряє, з ким тобі пропонують торгувати. Алерт
                приходить у Telegram, коли зв'язка пройшла обидві перевірки.
              </p>

              {/* На телефоні кнопки на всю ширину й у стовпчик: поруч вони
                  виходили вузькими, і головна дія переставала виглядати
                  головною. З sm повертаємось у рядок. */}
              <div className="flex flex-col sm:flex-row sm:flex-wrap gap-3">
                <Link
                  to="/login"
                  className="pulse-cta group inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-4 sm:py-3.5 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-colors shadow-lg shadow-accent-500/25"
                >
                  Почати
                  <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
                </Link>
                <Link
                  to="/how-it-works"
                  className="inline-flex items-center justify-center gap-2 w-full sm:w-auto px-6 py-4 sm:py-3.5 rounded-2xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-white font-bold transition-colors"
                >
                  Як це працює
                </Link>
              </div>

              <div className="mt-9">
                <div className="text-[11px] uppercase tracking-widest text-slate-600 mb-3">
                  Майданчики
                </div>
                <div className="flex flex-wrap gap-2">
                  {EXCHANGES.map(name => (
                    <span
                      key={name}
                      className="px-3 py-1.5 rounded-lg bg-slate-900/70 border border-slate-800 text-xs font-medium text-slate-400 hover:border-accent-500/30 hover:text-slate-300 transition-colors"
                    >
                      {name}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="animate-rise lg:pl-6">
              <SpreadVisual />
            </div>
          </div>
        </Section>
      </div>

      {/*
        Три опори. Свідомо БЕЗ спільного контейнера і на іншій фактурі,
        ніж сусідні секції: якщо всі блоки — картка з бордером, сторінка
        читається як одна сіра стрічка незалежно від текстів.
      */}
      <Section className="py-10 sm:py-14">
        <div className="grid md:grid-cols-3 gap-4">
            {PILLARS.map((pillar, i) => {
              const Icon = pillar.icon;
              return (
                <Reveal key={pillar.title} delay={i * 90}>
                  <GlowCard className="h-full bg-slate-950/40 border border-slate-800/60 backdrop-blur-sm rounded-3xl px-6 py-8 sm:px-8 hover:border-accent-500/25 hover:bg-slate-950/60 transition-all">
                    {/*
                      Плитка іконки навмисно велика й світиться: у сітці з
                      трьох колонок вона єдина дає вертикальний акцент і
                      не дає блокам злитись у суцільний текст.
                    */}
                    <div className="flex items-start justify-between mb-5">
                      <div className="w-14 h-14 rounded-2xl bg-accent-500/12 border border-accent-500/25 flex items-center justify-center shrink-0 shadow-lg shadow-accent-500/10">
                        <Icon className="w-7 h-7 text-accent-400" />
                      </div>
                      <span className="tag-mono text-[10px] text-slate-700">
                        0{i + 1}
                      </span>
                    </div>
                    <h3 className="text-lg font-bold text-white mb-2">{pillar.title}</h3>
                    <p className="text-sm text-slate-400 leading-relaxed mb-5">{pillar.text}</p>
                    <PillarVisual kind={pillar.visual} />
                  </GlowCard>
                </Reveal>
              );
            })}
        </div>
      </Section>

      <Rule />

      {/* ─── Чому не просто «найдешевше й найдорожче» ──────────────────── */}
      <Section className="py-12 sm:py-16">
        <Reveal>
          <Surface kind="scan" className="p-7 sm:p-14" noise>
            <DataRain count={16} />
            {/* Червоний натяк під блоком про ризик — рівно щоб змінити
                настрій секції, без блюру: градієнт і так м'який. */}
            <div
              className="absolute -right-32 -bottom-32 w-[30rem] h-[30rem] rounded-full pointer-events-none aurora-blob-2"
              style={{
                background:
                  'radial-gradient(circle at center, rgb(244 63 94 / 0.13) 0%, rgb(244 63 94 / 0.05) 40%, transparent 70%)',
              }}
            />

            <div className="relative z-10 grid lg:grid-cols-2 gap-10 items-center">
              {/* min-w-0 на обох колонках: на мобілці сітка в одну колонку,
                  і ширину треку задає найширший вміст. Рядки вердикту з
                  нерозривними бейджами розпирали його, через що різало і
                  бейджі, і абзац у сусідній колонці. */}
              <div className="min-w-0">
                <div className="mb-5">
                  <MonoTag>risk_engine</MonoTag>
                </div>
                <SectionHeading
                  num="01"
                  eyebrow="Головна відмінність"
                  title="Найкращий курс часто найнебезпечніший"
                  description="Верх склянки — це не завжди вигода. Там регулярно стоять ті, хто працює з чужих реквізитів, вимагає чек перед відпуском або тисне апеляцією. Сканер розбирає умови, поведінку й відгуки мерчанта до того, як ти побачиш зв'язку."
                />
                <Link
                  to="/security"
                  className="group inline-flex items-center gap-2 text-sm font-bold text-accent-400 hover:text-accent-300 transition-colors"
                >
                  Як влаштований антифрод
                  <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
                </Link>
              </div>

              <div className="space-y-3 min-w-0">
                {RISK_SIGNALS.map((row, i) => (
                  <Reveal key={row.title} delay={i * 80}>
                    <VerdictRow {...row} />
                  </Reveal>
                ))}
              </div>
            </div>
          </Surface>
        </Reveal>
      </Section>

      <Rule />

      {/* ─── Що саме перевіряється ────────────────────────────────────── */}
      <Section className="py-12 sm:py-16">
        <Reveal>
          <TrustStrip />
        </Reveal>
      </Section>

      <Rule />

      {/* ─── Можливості ───────────────────────────────────────────────── */}
      <Section className="py-12 sm:py-16">
        <Reveal>
          <SweepFrame className="mb-12 rounded-2xl">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <BracketMetric value="7" label="майданчиків" hint="Скануються одночасно" />
              <BracketMetric value="6" label="сигналів ризику" hint="Умови, поведінка, LLM, негатив, тексти, клони" />
              <BracketMetric value="5" label="режимів" hint="Спред, тейкер ×2, мейкер ×2" />
              <BracketMetric value="8" label="лімітів на банк" hint="Добові, місячні, разові, кількість" />
            </div>
          </SweepFrame>

          <SectionHeading
            num="03"
            eyebrow="Що всередині"
            title="Не тільки пошук спредів"
            description="Сканер, ризик-движок, облік карток і аналітика працюють як одна система — і керуються з Telegram або з вебдашборду."
          />
        </Reveal>

        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {FEATURES.map((feature, i) => {
            const Icon = feature.icon;
            return (
              <Reveal key={feature.title} delay={(i % 3) * 80}>
                <GlowCard className="h-full bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5 hover:bg-slate-900/70 transition-colors">
                  <Icon className="w-5 h-5 text-accent-400 mb-3" />
                  <h3 className="font-bold text-white mb-1.5">{feature.title}</h3>
                  <p className="text-sm text-slate-400 leading-relaxed">{feature.text}</p>
                </GlowCard>
              </Reveal>
            );
          })}
        </div>

        <Reveal className="mt-6">
          <Surface kind="grid" className="p-6 sm:p-8">
            <div className="grid lg:grid-cols-[1fr_auto] gap-8 items-center">
              <div>
                <div className="mb-4">
                  <MonoTag>alert_preview</MonoTag>
                </div>
                <h3 className="text-xl font-bold text-white mb-2">
                  Ось так виглядає знахідка
                </h3>
                <p className="text-sm text-slate-400 leading-relaxed max-w-md">
                  Кожен алерт приходить уже з вердиктом по обох мерчантах,
                  порахованим чистим спредом і кнопками просто в застосунок
                  біржі. Нічого перевіряти руками не треба.
                </p>
              </div>

              <AlertPreview />
            </div>
          </Surface>
        </Reveal>

        <Reveal className="mt-8">
          <Link
            to="/features"
            className="group inline-flex items-center gap-2 text-sm font-bold text-accent-400 hover:text-accent-300 transition-colors"
          >
            Усі можливості детально
            <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </Reveal>
      </Section>

      <Rule />

      {/* ─── FAQ ──────────────────────────────────────────────────────── */}
      <Section className="py-12 sm:py-16">
        <Faq />
      </Section>

      <Rule />

      {/* ─── Заклик ───────────────────────────────────────────────────── */}
      <Section className="py-12 sm:py-16">
        <Reveal>
          <GlowCard className="bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl rounded-[2.5rem] p-8 sm:p-14 text-center shadow-2xl hover:border-accent-500/40 transition-all">
            <div className="relative z-10">
              <h2 className="text-3xl sm:text-4xl font-bold text-white tracking-tight mb-4">
                Підключається за хвилину
              </h2>
              <p className="text-slate-400 max-w-lg mx-auto mb-8 leading-relaxed">
                Вхід через той самий Telegram, у якому працює бот. Фільтри,
                картки й ключі бірж підтягнуться самі — нічого переносити руками
                не доведеться.
              </p>
              <Link
                to="/login"
                className="pulse-cta group inline-flex items-center gap-2 px-8 py-4 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-all shadow-xl shadow-accent-500/25"
              >
                Увійти через Telegram
                <ArrowRight className="w-5 h-5 transition-transform group-hover:translate-x-0.5" />
              </Link>
            </div>
          </GlowCard>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}

/**
 * Превʼю алерта. Не скріншот, а верстка: масштабується, тримає гаму й
 * важить нуль. Дані ілюстративні — підпис це говорить прямо.
 */
function AlertPreview() {
  return (
    <div className="w-full lg:w-80 shrink-0 bg-slate-950/90 border border-slate-800 rounded-2xl p-4 shadow-2xl shadow-slate-950/60">
      <div className="flex items-center justify-between mb-3 pb-3 border-b border-slate-800">
        <span className="tag-mono text-[10px] text-slate-500">приклад алерта</span>
        <span className="text-lg font-black text-accent-400 tabular-nums leading-none">
          +1.61%
        </span>
      </div>

      <div className="space-y-2.5">
        {[
          ['Купівля', 'Bybit', '41.02 ₴', 'ok'],
          ['Продаж', 'OKX', '41.68 ₴', 'warn'],
        ].map(([side, ex, price, verdict]) => (
          <div key={side as string} className="flex items-center gap-2.5">
            <span className="tag-mono text-[10px] text-slate-600 w-14 shrink-0">{side}</span>
            <span className="text-xs font-bold text-slate-200 flex-1 truncate">{ex}</span>
            <span className="text-xs tabular-nums text-slate-300">{price}</span>
            <span
              className={cn(
                'w-1.5 h-1.5 rounded-full shrink-0',
                verdict === 'ok' ? 'bg-accent-500' : 'bg-orange-500'
              )}
            />
          </div>
        ))}
      </div>

      <div className="mt-3 pt-3 border-t border-slate-800 flex items-center justify-between text-[11px]">
        <span className="text-slate-500">Чистими</span>
        <span className="font-bold text-slate-200 tabular-nums">+412 ₴</span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="text-center py-2 rounded-lg bg-accent-500/10 border border-accent-500/25 text-[11px] font-bold text-accent-400">
          Відкрити
        </div>
        <div className="text-center py-2 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-bold text-slate-400">
          Бан
        </div>
      </div>
    </div>
  );
}

/**
 * Міні-візуалізація опори. Показує предмет розмови замість того, щоб
 * описувати його ще одним реченням: сім назв бірж, шкалу ризику,
 * заповнені ліміти.
 */
function PillarVisual({ kind }: { kind: 'exchanges' | 'risk' | 'limits' }) {
  if (kind === 'exchanges') {
    return (
      <div className="flex flex-wrap gap-1.5">
        {EXCHANGES.map(name => (
          <span
            key={name}
            className="tag-mono px-2 py-1 rounded-md bg-slate-900 border border-slate-800 text-[10px] text-slate-400"
          >
            {name}
          </span>
        ))}
      </div>
    );
  }

  if (kind === 'risk') {
    return (
      <div className="space-y-2">
        {[
          ['Умови', 82],
          ['Поведінка', 64],
          ['Відгуки', 45],
          ['Списки', 96],
        ].map(([label, pct]) => (
          <div key={label as string} className="flex items-center gap-2.5">
            <span className="tag-mono text-[10px] text-slate-500 w-20 shrink-0">{label}</span>
            <div className="flex-1 h-1 bg-slate-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-accent-500/70 rounded-full"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {[
        ['monobank', 68],
        ['privatbank', 34],
        ['пумб', 91],
      ].map(([bank, pct]) => (
        <div key={bank as string}>
          <div className="flex justify-between text-[10px] mb-1">
            <span className="tag-mono text-slate-500">{bank}</span>
            <span className={cn('tabular-nums', (pct as number) > 85 ? 'text-orange-400' : 'text-slate-500')}>
              {pct}%
            </span>
          </div>
          <div className="h-1 bg-slate-800 rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full',
                (pct as number) > 85 ? 'bg-orange-500/80' : 'bg-accent-500/60'
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

