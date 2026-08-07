import React from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight, Search, Filter, ShieldCheck, Send, Wallet,
  Clock, Landmark, SplitSquareHorizontal, ExternalLink,
  TrendingDown, WalletMinimal, UserRoundX,
} from 'lucide-react';
import { cn } from '../lib/utils';
import LandingLayout, { Section, SectionHeading, Rule } from './LandingLayout';
import { Reveal, useReveal, useScrollDraw } from './motion';
import { Surface, MonoTag, Card } from './surfaces';
import { StrategyDiagram, RouteLine } from './blocks';
import ScanField from './ScanField';

/**
 * Конвеєр обробки — від опитування бірж до алерту.
 *
 * Порядок кроків тут не декоративний: він відповідає реальному циклу
 * сканера. Ризик-перевірка стоїть після пошуку зв'язки саме тому, що
 * перевіряти кожного мерчанта на біржі дорого — спершу відбираються
 * кандидати, і лише вони йдуть у ризик-движок.
 */
const STEPS = [
  {
    icon: Search,
    title: 'Опитування майданчиків',
    text:
      'Сканер знімає P2P-склянки з усіх увімкнених бірж. Біржа, яка почала відмовляти, автоматично йде в cooldown, щоб не тягнути за собою весь цикл.',
    detail: 'усі біржі паралельно',
    /*
     * Скільки лишається після кроку. Числа — ті самі, що в лійці на
     * «Можливостях», тож дві сторінки не розходяться. Крок звірки з
     * картками кількість не змінює: він ріже ОБСЯГ, і показувати там
     * число означало б вигадати відсів, якого немає.
     */
    left: 1240,
  },
  {
    icon: Filter,
    title: 'Твої фільтри',
    text:
      'З усього обсягу лишається те, що підходить під твій капітал, банки, пороги мерчанта й режим роботи. Це відсіює переважну більшість ордерів ще до дорогих перевірок.',
    detail: 'капітал · банки · пороги',
    left: 186,
  },
  {
    icon: ShieldCheck,
    title: 'Перевірка контрагента',
    text:
      'Кандидати йдуть у ризик-движок: розбір тексту умов, поведінка в часі, відгуки, чорні списки. Результат — оцінка ризику від 0 до 100 і вердикт: чисто, підозріло або заблоковано.',
    detail: 'правила · поведінка · ШІ',
    left: 14,
  },
  {
    icon: Wallet,
    title: 'Звірка з картками',
    text:
      'Обсяг зіставляється з реальними лімітами твоїх банків: добовими, місячними й на одну транзакцію. Те, що не проходить, або зменшується, або відсікається.',
    detail: 'ліміти · залишки',
    volumeOnly: true,
  },
  {
    icon: Send,
    title: 'Алерт',
    text:
      'Те, що пройшло всі етапи, приходить у Telegram з кнопками просто в застосунок біржі. Однакові зв\'язки схлопуються, щоб не заливати чат.',
    detail: 'Telegram · дашборд',
    left: 3,
  },
];

/** Найбільше значення лійки — від нього рахуються всі частки. */
const FUNNEL_TOP = 1240;

const MODES: { name: string; kind: 'spread' | 'taker' | 'maker'; text: string; note: string }[] = [
  {
    name: 'Спред',
    kind: 'spread',
    text: 'Класична зв\'язка: купив дешевше на одній біржі, продав дорожче на іншій. Сканер рахує чистий спред уже після комісій і мережевого переказу.',
    note: 'SPREAD',
  },
  {
    name: 'Тейкер',
    kind: 'taker',
    text: 'Полювання на чужі оголошення в один бік — тільки купівля або тільки продаж. Корисно, коли треба зайти або вийти за конкретною ціною.',
    note: 'TAKER_BUY · TAKER_SELL',
  },
  {
    name: 'Мейкер',
    kind: 'maker',
    text: 'Порада ціни для власного оголошення. Враховує стінки ліквідності: ставити перед великим обсягом конкурентів немає сенсу, ти просто не продаси.',
    note: 'MAKER_BUY · MAKER_SELL',
  },
];

/**
 * Крок конвеєра на спільній осі.
 *
 * Раніше кроки були просто стосом однакових карток — і сторінка читалась
 * як список, хоча описує рух даних. Тепер вузол сидить на лінії, а лінія
 * промальовується зверху вниз у міру прокрутки: видно напрямок, а не
 * перелік.
 *
 * На вузьких екранах вісь притиснута ліворуч — зигзаг там не читається,
 * картки стають надто вузькими.
 */
const Step: React.FC<{
  step: (typeof STEPS)[number];
  index: number;
  isLast: boolean;
}> = ({ step, index, isLast }) => {
  const Icon = step.icon;
  const { ref, shown } = useReveal<HTMLDivElement>();
  const { trackRef, fillRef, litRef, cardRef } = useScrollDraw<HTMLDivElement>();
  const onLeft = index % 2 === 0;

  return (
    <div
      ref={ref}
      className="grid grid-cols-[auto_1fr] lg:grid-cols-[1fr_auto_1fr] gap-x-5 lg:gap-x-10"
    >
      {/*
        Колонка осі — вона ж мірило прогресу. Заповнення рахується від її
        власного положення у вікні, а не від індексу кроку: картки різної
        висоти, і рівні частки розсинхронізували б лінію з іконками.
      */}
      <div
        ref={trackRef}
        className="relative col-start-1 lg:col-start-2 row-start-1 flex flex-col items-center"
      >
        <div className="relative z-10 w-12 h-12 lg:w-14 lg:h-14 rounded-2xl flex items-center justify-center shrink-0 bg-slate-950 border border-slate-800">
          <Icon className="w-5 h-5 lg:w-6 lg:h-6 text-slate-700" />

          {/*
            Увімкнений стан — окремий шар поверх згаслого. Так прогрес
            пишеться в opacity напряму, без стану React: інакше кожен
            кадр прокрутки давав би ререндер п'яти кроків.
          */}
          <span
            ref={litRef}
            className="absolute inset-0 rounded-2xl border border-accent-500/50 flex items-center justify-center opacity-0 transition-opacity duration-500"
            style={{
              boxShadow:
                '0 0 0 6px rgb(var(--accent-rgb) / 0.06), 0 0 30px rgb(var(--accent-rgb) / 0.25)',
            }}
            aria-hidden
          >
            <Icon className="w-5 h-5 lg:w-6 lg:h-6 text-accent-400" />
          </span>
        </div>

        {/*
          Відрізок до наступного вузла. Росте через scaleY — це transform,
          тож кадр малює композитор і розкладка не перераховується.
        */}
        {!isLast && (
          <div className="relative flex-1 w-px my-2 bg-slate-800/70 overflow-hidden">
            <div
              ref={fillRef as React.RefObject<HTMLDivElement>}
              className="absolute inset-0 origin-top"
              style={{
                transform: 'scaleY(0)',
                background:
                  'linear-gradient(to bottom, rgb(var(--accent-rgb) / 0.75), rgb(var(--accent-rgb) / 0.2))',
              }}
            />
          </div>
        )}
      </div>

      {/*
        Картка кроку — на десктопі поперемінно ліворуч і праворуч від осі.
        Текст усередині лишається вирівняним ліворуч навіть у лівій колонці:
        симетрія тут виглядала б охайніше, але абзац на три рядки по
        правому краю читається помітно гірше.
      */}
      <div
        className={cn(
          'col-start-2 row-start-1 pb-8 lg:pb-10',
          onLeft ? 'lg:col-start-1' : 'lg:col-start-3'
        )}
      >
        {/*
          Фактура через крок тут була, і саме вона робила рейку строкатою:
          кроки 02 і 05 мали крапки, решта — ні, хоча всі п'ять рівнозначні.
          Поверхня тепер спільна (див. `Card`), а вирізняє крок те, що й
          має, — номер, іконка на осі й підсвітка при прокрутці.
        */}
        <Card
          ref={cardRef}
          className={cn(
            'pipe-card reveal inline-block w-full p-5 sm:p-6',
            shown && 'reveal-in'
          )}
          style={{ transitionDelay: `${index * 60}ms` }}
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 mb-2">
            <span className="tag-mono text-xs font-black text-accent-500 tabular-nums">
              0{index + 1}
            </span>
            <h3 className="text-lg font-bold text-white">{step.title}</h3>
          </div>

          <p className="text-sm text-slate-400 leading-relaxed">{step.text}</p>

          {/*
            Смуга відсіву.

            Перша версія була незрозумілою, і це справедливий закид: смуга
            з підписом «лишається 14» праворуч не каже ні від чого
            рахується, ні чому сусідня заповнена інакше. Тепер підпис
            стоїть ПЕРЕД смугою й називає обидва числа — «14 із 1240», —
            а крок, що ріже не кількість, а суму, і сформульований інакше,
            без смуги взагалі: однакова смуга з іншим значенням і була
            головним джерелом плутанини.
          */}
          <div className="mt-4 rounded-lg bg-slate-950/70 border border-slate-800 px-3 py-2.5">
            {step.volumeOnly ? (
              <div className="text-[11px] text-slate-400 leading-snug">
                Кількість зв'язок не змінюється — ріжеться{' '}
                <span className="text-slate-200 font-bold">сума кожної</span> під те, що
                лишилось на картках.
              </div>
            ) : (
              <>
                <div className="flex items-baseline justify-between gap-2 mb-1.5">
                  <span className="text-[11px] text-slate-400">лишається пропозицій</span>
                  <span className="tag-mono text-[11px] text-slate-200 tabular-nums shrink-0">
                    {step.left} із {FUNNEL_TOP}
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-slate-900 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent-500"
                    style={{ width: `${Math.max(1.5, ((step.left ?? 0) / FUNNEL_TOP) * 100)}%` }}
                  />
                </div>
              </>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="tag-mono inline-block text-[10px] text-slate-400 px-2 py-1 rounded-md bg-slate-950/80 border border-slate-800">
              {step.detail}
            </span>
          </div>
        </Card>
      </div>
    </div>
  );
};

/** Рядок у прев'ю алерта — щоб не повторювати ту саму розмітку п'ять разів. */
const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-center justify-between gap-3">
    <span className="text-slate-400">{label}</span>
    <span className="text-right">{children}</span>
  </div>
);

/**
 * Прев'ю того, що реально приходить у Telegram.
 *
 * Значення взяті з форматів бота — вердикти OK/SUSPICIOUS/BLOCK, ролі
 * Taker/Maker, назви бірж і банків. Це ілюстрація, тому вона підписана як
 * приклад: підставити сюди правдоподібне «живе» число означало б показати
 * прибуток, якого система нікому не обіцяє.
 */
const AlertPreview: React.FC = () => (
  <div className="bg-slate-950 border border-slate-800 rounded-2xl overflow-hidden shadow-2xl shadow-slate-950/60">
    <div className="flex items-center justify-between gap-3 px-4 py-3 bg-slate-900/80 border-b border-slate-800">
      <div className="flex items-center gap-2.5 min-w-0">
        <span className="w-7 h-7 rounded-lg bg-accent-500/15 border border-accent-500/30 flex items-center justify-center shrink-0">
          <Send className="w-3.5 h-3.5 text-accent-400" />
        </span>
        <span className="text-xs font-bold text-slate-200 truncate">P2PTraderInfo_bot</span>
      </div>
      <span className="tag-mono text-[10px] text-slate-400 flex items-center gap-1 shrink-0">
        <Clock className="w-3 h-3" />
        18:42
      </span>
    </div>

    <div className="p-4 space-y-3.5 text-xs">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xl font-black text-accent-400 tabular-nums">+2.15%</span>
        <span className="tag-mono text-[10px] uppercase tracking-widest text-slate-400">
          спред
        </span>
      </div>

      {/*
        Маршрут окремим рядком над колонками. З самих колонок напрямок
        доводилось збирати очима: «Купівля · OKX» ліворуч, «Продаж ·
        Binance» праворуч — тобто читач мав здогадатись, що це одна
        зв'язка, а не два незалежні рядки.
      */}
      <RouteLine from="OKX" to="Binance" />

      <div className="grid grid-cols-2 gap-px bg-slate-800 rounded-xl overflow-hidden">
        <div className="bg-slate-900/90 p-3">
          <div className="text-[9px] uppercase tracking-wider text-slate-400 mb-1">Купівля · OKX</div>
          <div className="text-sm font-bold text-slate-200 tabular-nums">41.15 ₴</div>
          <div className="text-[10px] text-slate-400 mt-0.5">Taker · ПриватБанк</div>
        </div>
        <div className="bg-slate-900/90 p-3">
          <div className="text-[9px] uppercase tracking-wider text-slate-400 mb-1">Продаж · Binance</div>
          <div className="text-sm font-bold text-slate-200 tabular-nums">42.03 ₴</div>
          <div className="text-[10px] text-slate-400 mt-0.5">Taker · monobank</div>
        </div>
      </div>

      <div className="space-y-2 pt-1">
        <Row label="Вердикт">
          <span className="px-2 py-0.5 rounded bg-accent-500/15 text-accent-400 font-bold text-[10px] tag-mono">
            OK · ризик 0/100
          </span>
        </Row>
        <Row label="Контрагент">
          <span className="text-slate-300">1840 угод · 99.4%</span>
        </Row>
        <Row label="Обсяг під ліміти">
          <span className="text-slate-300 tabular-nums">24 000 ₴</span>
        </Row>
      </div>

      <div className="flex gap-2 pt-1.5">
        <button className="flex-1 py-2 bg-accent-500 hover:bg-accent-400 text-slate-950 font-bold text-[11px] rounded-xl transition-colors flex items-center justify-center gap-1.5">
          <ExternalLink className="w-3 h-3" />
          Відкрити на OKX
        </button>
        <button className="flex-1 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold text-[11px] rounded-xl transition-colors border border-slate-700/60 flex items-center justify-center gap-1.5">
          <SplitSquareHorizontal className="w-3 h-3" />
          Розбити по картках
        </button>
      </div>
    </div>
  </div>
);

const ALERT_NOTES = [
  {
    icon: ShieldCheck,
    title: 'Вердикт, а не просто рейтинг',
    text: 'Бал ризику рахує движок: 0 для чистого мерчанта, 30 для підозрілого, 100 для заблокованого. Рейтинг біржі — лише один із вхідних сигналів.',
  },
  {
    icon: Landmark,
    title: 'Обсяг уже підігнаний',
    text: 'Сума в алерті — не обсяг оголошення, а те, що реально проходить під твої добові й місячні ліміти на момент сповіщення.',
  },
  {
    icon: ExternalLink,
    title: 'Кнопки ведуть в біржу',
    text: 'Deeplink відкриває конкретне оголошення в застосунку. Сканер нічого не натискає за тебе — ордер створюєш ти.',
  },
];

export default function HowItWorksPage() {
  return (
    <LandingLayout>
      {/*
        Потік даних за шапкою. Тут доречний саме він, а не скануючий
        промінь із безпеки: сторінка про рух даних конвеєром, і фон має
        текти вниз разом із ним.
      */}
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[40rem] overflow-hidden"
        aria-hidden
      >
        <ScanField variant="flow" className="w-full h-full opacity-60" />
      </div>

      <Section className="pt-16 sm:pt-20 relative">
        <SectionHeading
          eyebrow="Як це працює"
          title="Шлях від склянки до алерту"
          description="П'ять етапів. Дешеві перевірки йдуть першими й зрізають основний обсяг, щоб важкий розбір мерчанта запускався лише на тому, що вже пройшло твої фільтри. Число в кожному кроці показує, скільки пропозицій лишається після нього — на прикладі одного циклу з 1240 знайдених."
        />

        <div className="mt-4">
          {STEPS.map((step, i) => (
            <Step key={step.title} step={step} index={i} isLast={i === STEPS.length - 1} />
          ))}
        </div>
      </Section>

      <Rule />

      <Section className="pt-0">
        <Reveal>
          <SectionHeading
            eyebrow="Результат"
            title="Ось що приходить у Telegram"
            description="Одне повідомлення замість вкладки з десятком відкритих бірж. Нижче — приклад із заповненими полями, щоб було видно склад алерта."
          />
        </Reveal>

        <div className="grid lg:grid-cols-12 gap-8 items-start">
          <div className="lg:col-span-5">
            <Reveal>
              <div className="mb-3">
                <MonoTag>приклад</MonoTag>
              </div>
              <AlertPreview />
            </Reveal>
          </div>

          <div className="lg:col-span-7 space-y-4">
            {ALERT_NOTES.map((note, i) => {
              const Icon = note.icon;
              return (
                <Reveal key={note.title} delay={i * 90}>
                  <Card className="flex gap-4 p-5 sm:p-6">
                    <span className="w-10 h-10 rounded-xl bg-accent-500/10 border border-accent-500/20 flex items-center justify-center shrink-0">
                      <Icon className="w-5 h-5 text-accent-400" />
                    </span>
                    <div className="min-w-0">
                      <h3 className="text-base font-bold text-white mb-1.5">{note.title}</h3>
                      <p className="text-sm text-slate-400 leading-relaxed">{note.text}</p>
                    </div>
                  </Card>
                </Reveal>
              );
            })}
          </div>
        </div>
      </Section>

      <Rule />

      <Section className="pt-0">
        <Reveal>
          <SectionHeading
            eyebrow="Стратегії"
            title="Три способи торгівлі"
            description="Режим перемикається під поточні задачі — сканер по-різному розраховує ліквідність у кожному з них. У коді це п'ять значень: спред плюс по два напрямки для тейкера й мейкера."
          />
        </Reveal>

        <div className="grid md:grid-cols-3 gap-6">
          {MODES.map((mode, i) => (
            <Reveal key={mode.name} delay={i * 90}>
              <Card className="h-full p-7 backdrop-blur-xl shadow-xl">
                {/*
                  Схема тут не прикраса: словами різниця між режимами
                  звучить майже однаково, а на малюнку видно одразу —
                  коло між біржами, вхід у чужу склянку, власна ціна
                  перед стінкою.
                */}
                <div className="mb-5 -mx-2">
                  <StrategyDiagram kind={mode.kind} />
                </div>

                <div className="flex items-baseline gap-2.5 mb-2">
                  <span className="tag-mono text-xs font-black text-accent-500 tabular-nums">
                    0{i + 1}
                  </span>
                  <h3 className="text-xl font-bold text-white">{mode.name}</h3>
                </div>

                <p className="text-sm text-slate-400 leading-relaxed mb-4">{mode.text}</p>

                <span className="tag-mono inline-block text-[10px] text-slate-400 px-2 py-1 rounded-md bg-slate-950/80 border border-slate-800">
                  {mode.note}
                </span>
              </Card>
            </Reveal>
          ))}
        </div>
      </Section>

      <Rule />

      <Section className="pt-0 pb-16">
        <Reveal>
          <Surface kind="scan" className="p-8 sm:p-12">
            <h2 className="text-2xl font-bold text-white mb-6">Чого сканер не робить</h2>

            {/*
              Три межі однаково важливі, тож і ваги мають бути однакові.
              Маркований список цього не давав: очі бігли по ньому як по
              дрібному шрифту, хоча це найчесніша частина сторінки.

              Іконка перекреслена — знак заборони читається швидше за
              будь-яке «не» на початку речення.

              Текст свідомо без цифр. Тут раніше стояло «відсіює 99%
              шахрайських схем»: такої метрики ніхто не міряв, і вигадане
              число саме в абзаці про чесність — найгірше місце з можливих.
            */}
            <div className="grid sm:grid-cols-3 gap-4">
              {[
                {
                  icon: TrendingDown,
                  title: 'Не гарантує прибуток',
                  text: 'Спред живе секунди. Поки ти відкриваєш ордер, ціна може змінитись або оголошення зникне.',
                },
                {
                  icon: WalletMinimal,
                  title: 'Не рухає твої гроші',
                  text: 'Доступу до переказів немає: ордер створюєш і підтверджуєш ти сам.',
                },
                {
                  icon: UserRoundX,
                  title: 'Не робить контрагента чесним',
                  text: 'Ловить відомі схеми — боти, підміну умов, скарги, чорні списки. Чиста історія не гарантує нічого.',
                },
              ].map(item => {
                const Icon = item.icon;
                return (
                  <div
                    key={item.title}
                    className="rounded-2xl bg-slate-950/60 border border-amber-500/20 p-5"
                  >
                    <span className="relative w-10 h-10 rounded-xl bg-amber-500/10 border border-amber-500/25 flex items-center justify-center mb-4">
                      <Icon className="w-5 h-5 text-amber-400" />
                    </span>
                    <h3 className="text-sm font-bold text-white mb-1.5">{item.title}</h3>
                    <p className="text-[13px] text-slate-400 leading-relaxed">{item.text}</p>
                  </div>
                );
              })}
            </div>

            <Link
              to="/security"
              className="inline-flex items-center gap-2 mt-8 px-6 py-3 rounded-xl bg-slate-800/80 hover:bg-slate-700/80 text-accent-400 text-sm font-bold transition-all border border-slate-700/60"
            >
              Як влаштована перевірка мерчантів
              <ArrowRight className="w-4 h-4" />
            </Link>
          </Surface>
        </Reveal>
      </Section>
    </LandingLayout>
  );
}
