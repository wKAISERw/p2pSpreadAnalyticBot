import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, ShieldCheck, Radar, CreditCard, Bell, Brain, LineChart,
  Layers, Bot, Clock,
} from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading } from './LandingLayout';
import SpreadVisual from './SpreadVisual';
import { Reveal, GlowCard } from './motion';
import { Surface, DataRain, MonoTag, Metric } from './surfaces';

const EXCHANGES = ['Binance', 'Bybit', 'OKX', 'MEXC', 'Wallet', 'BingX', 'CryptoBot'];

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
      {/* ─── Герой ─────────────────────────────────────────────────────── */}
      <div className="relative overflow-hidden">
        {/*
          Локальних плям тут більше немає — фон малює AuroraField на рівні
          каркаса. Три великі blur-шари поверх нього були б подвійною
          роботою для GPU за той самий візуальний результат.
        */}
        <Section className="relative pt-14 sm:pt-20 pb-10">
          <div className="hero-drift grid lg:grid-cols-2 gap-10 lg:gap-16 items-center">
            <div className="animate-rise">
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-900/80 border border-slate-800 text-xs text-slate-400 mb-6 backdrop-blur-sm">
                <span className="w-1.5 h-1.5 rounded-full bg-accent-500" />
                P2P-арбітраж для українського ринку
              </div>

              <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-white tracking-tight leading-[1.05] mb-6">
                Спред видно всім.
                <br />
                <span className="text-accent-400">Ризик — ні.</span>
              </h1>

              <p className="text-lg text-slate-400 leading-relaxed mb-8 max-w-lg">
                Arbix Quantum знаходить різницю курсів між P2P-майданчиками
                й одразу перевіряє, з ким тобі пропонують торгувати. Алерт
                приходить у Telegram, коли зв'язка пройшла обидві перевірки.
              </p>

              <div className="flex flex-wrap gap-3">
                <Link
                  to="/login"
                  className="group inline-flex items-center gap-2 px-6 py-3.5 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-colors shadow-lg shadow-accent-500/25"
                >
                  Почати
                  <ArrowRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
                </Link>
                <Link
                  to="/how-it-works"
                  className="inline-flex items-center gap-2 px-6 py-3.5 rounded-2xl bg-slate-900 hover:bg-slate-800 border border-slate-800 text-white font-bold transition-colors"
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
                    <div className="flex items-center gap-3 mb-4">
                      <div className="w-9 h-9 rounded-lg bg-accent-500/15 border border-accent-500/25 flex items-center justify-center shrink-0">
                        <Icon className="w-4.5 h-4.5 text-accent-400" />
                      </div>
                      <span className="tag-mono text-[10px] text-slate-600">
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
              <div>
                <div className="mb-5">
                  <MonoTag>risk_engine</MonoTag>
                </div>
                <SectionHeading
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

              <div className="space-y-3">
                {[
                  ['block', 'Оплата з чужих реквізитів', 'Ознака трикутника'],
                  ['warn', 'Просить чек перед відпуском', 'Тиск на апеляцію'],
                  ['warn', 'Ліміти не змінюються 40 циклів', 'Схоже на бота'],
                  ['ok', '1840 угод, 99.2% завершення', 'Скарг немає'],
                ].map(([verdict, title, meta], i) => (
                  <Reveal key={title} delay={i * 80}>
                    <RiskRow verdict={verdict as 'ok' | 'warn' | 'block'} title={title} meta={meta} />
                  </Reveal>
                ))}
              </div>
            </div>
          </Surface>
        </Reveal>
      </Section>

      {/* ─── Можливості ───────────────────────────────────────────────── */}
      <Section className="py-12 sm:py-16">
        <Reveal>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-12">
            <Metric value="7" label="майданчиків" hint="Сканяться одночасно" />
            <Metric value="4" label="джерела ризику" hint="Regex, поведінка, відгуки, списки" />
            <Metric value="5" label="режимів" hint="Спред, тейкер ×2, мейкер ×2" />
            <Metric value="8" label="лімітів картки" hint="Добові, місячні, разові" />
          </div>

          <SectionHeading
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
                className="group inline-flex items-center gap-2 px-8 py-4 rounded-2xl bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold transition-all shadow-xl shadow-accent-500/25"
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

function RiskRow({
  verdict,
  title,
  meta,
}: {
  verdict: 'ok' | 'warn' | 'block';
  title: string;
  meta: string;
}) {
  const style = {
    ok: { dot: 'bg-accent-500', label: 'OK', cls: 'text-accent-400 border-accent-500/30' },
    warn: { dot: 'bg-orange-500', label: 'WARN', cls: 'text-orange-400 border-orange-500/30' },
    block: { dot: 'bg-red-500', label: 'BLOCK', cls: 'text-red-400 border-red-500/30' },
  }[verdict];

  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-2xl bg-slate-950/60 border border-slate-800/60 hover:border-slate-700 transition-colors">
      <span className={`w-2 h-2 rounded-full shrink-0 ${style.dot}`} />
      <div className="min-w-0 flex-1">
        <div className="text-sm text-slate-200 truncate">{title}</div>
        <div className="text-[11px] text-slate-500">{meta}</div>
      </div>
      <span
        className={`shrink-0 px-2 py-0.5 rounded-md border text-[10px] font-black tracking-wider ${style.cls}`}
      >
        {style.label}
      </span>
    </div>
  );
}
