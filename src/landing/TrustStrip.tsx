import React from 'react';
import { ShieldCheck, Radar, Layers, CreditCard, Check } from 'lucide-react';
import { Surface, MonoTag } from './surfaces';
import { Reveal } from './motion';

/**
 * Траст-блок.
 *
 * Свідомо без відгуків. Придумати «Олексій, +40% за місяць» на
 * фінансовому продукті означає сфабрикувати соціальний доказ, який
 * підштовхує людину ризикувати грошима. Коли зʼявляться справжні
 * користувачі — будуть їхні справжні слова з дозволу.
 *
 * Натомість те, що можна показати чесно: скільки і чого система реально
 * перевіряє. Для аудиторії, яка вже в темі, конкретика працює краще за
 * похвалу.
 */

const CHECKS: { label: string; items: string[]; icon: React.ElementType }[] = [
  {
    icon: Radar,
    label: 'Перед показом зв\'язки',
    items: [
      'Ціна перевіряється на кількох обсягах',
      'Комісія біржі й мережевий переказ враховані',
      'Обидві ноги проходять пороги мерчанта',
    ],
  },
  {
    icon: ShieldCheck,
    label: 'Про контрагента',
    items: [
      'Текст умов проти набору правил',
      'Поведінка в часі: ліміти, швидкість, поповнення',
      'Негативні відгуки прочитані, не полічені',
      'Спільний і персональний чорні списки',
    ],
  },
  {
    icon: CreditCard,
    label: 'Про твої гроші',
    items: [
      'Обсяг звіряється з добовими й місячними лімітами',
      'Ключі бірж зберігаються зашифрованими',
      'Дані живуть на твоєму сервері, не в хмарі',
    ],
  },
];

export default function TrustStrip() {
  return (
    <Surface kind="grid" className="p-6 sm:p-10">
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-4">
          <span className="tag-mono text-xs font-bold text-slate-600 tabular-nums">/02</span>
          <MonoTag>what_is_checked</MonoTag>
        </div>
        <h2 className="text-2xl sm:text-3xl font-bold text-white tracking-tight mb-3">
          Що саме перевіряється
        </h2>
        <p className="text-sm text-slate-400 max-w-2xl leading-relaxed">
          Не «розумний алгоритм», а конкретний перелік. Кожен пункт — окрема
          перевірка, яку зв'язка має пройти, перш ніж ти про неї дізнаєшся.
        </p>
      </div>

      <div className="grid md:grid-cols-3 gap-px bg-slate-800/50 rounded-2xl overflow-hidden">
        {CHECKS.map((group, i) => {
          const Icon = group.icon;
          return (
            <Reveal key={group.label} delay={i * 80}>
              <div className="h-full bg-slate-950/80 p-6">
                <div className="flex items-center gap-2.5 mb-5">
                  <Icon className="w-4 h-4 text-accent-400 shrink-0" />
                  <span className="text-xs font-bold uppercase tracking-wider text-slate-300">
                    {group.label}
                  </span>
                </div>

                <ul className="space-y-3">
                  {group.items.map(item => (
                    <li key={item} className="flex gap-2.5 text-sm text-slate-400 leading-snug">
                      <Check className="w-3.5 h-3.5 text-accent-500 shrink-0 mt-0.5" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </Reveal>
          );
        })}
      </div>

      {/*
        Межі — частина довіри, а не дрібний шрифт унизу. Для аудиторії, що
        вже торгує, чесність про обмеження переконує сильніше за обіцянки.
      */}
      <div className="mt-6 flex items-start gap-3 px-5 py-4 rounded-2xl bg-orange-500/5 border border-orange-500/20">
        <Layers className="w-4 h-4 text-orange-400 shrink-0 mt-0.5" />
        <p className="text-xs text-slate-300 leading-relaxed">
          <span className="font-bold text-orange-400">Чого це не робить: </span>
          не гарантує прибуток і не прибирає ризик контрагента повністю. Спред
          живе секунди, а мерчант із чистою історією може повестися нечесно
          вперше саме з тобою. Інструмент зменшує ризик — рішення лишається за
          тобою.
        </p>
      </div>
    </Surface>
  );
}
