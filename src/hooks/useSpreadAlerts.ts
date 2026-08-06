import { useEffect, useRef } from 'react';
import useSWR from 'swr';
import { api } from '../services/api';
import { useAppStore } from '../store';
import { ArbitrageOpportunity } from '../types';

/**
 * Звуковий сигнал про новий спред.
 *
 * Раніше жив усередині Dashboard і мав чотири окремі поломки:
 *
 *  1. Компонент розмонтовувався при переході в інший розділ — разом із ним
 *     зникала підписка на /opportunities, тож на будь-якій сторінці, крім
 *     дашборду, звуку не було взагалі. Саме це й було помітно.
 *  2. На кожен сигнал створювався новий AudioContext і ніколи не
 *     закривався. Браузери тримають ліміт (у Chrome ~6 одночасних), тож
 *     після кількох алертів звук вимикався назовсім до перезавантаження.
 *  3. Новизна визначалась за ДОВЖИНОЮ масиву. Якщо за цикл зникло два
 *     спреди й з'явилось два нових — довжина та сама, сигналу немає.
 *     А `slice(0, delta)` ще й вважав, що нові стоять на початку, хоча
 *     сканер щоцикл перебудовує список у довільному порядку.
 *  4. AudioContext, створений без жесту користувача, стартує у стані
 *     suspended — його ніхто не відновлював.
 *
 * Хук викликається один раз в App, тому працює на всіх сторінках. Ключ
 * SWR той самий, що в Dashboard, тож зайвих запитів це не додає.
 */

let audioContext: AudioContext | null = null;

/** Один контекст на застосунок замість нового на кожен сигнал. */
function getAudioContext(): AudioContext | null {
  if (typeof window === 'undefined') return null;
  const Ctor = window.AudioContext || (window as any).webkitAudioContext;
  if (!Ctor) return null;

  if (!audioContext || audioContext.state === 'closed') {
    try {
      audioContext = new Ctor();
    } catch {
      return null;
    }
  }
  return audioContext;
}

function playChime(volume: number, urgent: boolean) {
  const ctx = getAudioContext();
  if (!ctx || ctx.state === 'suspended') return;

  try {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.connect(gain);
    gain.connect(ctx.destination);

    // Великий спред звучить вище й довше — щоб не вслухатись, який саме
    // сигнал щойно був.
    const from = urgent ? 1180 : 880;
    const to = urgent ? 660 : 440;
    const duration = urgent ? 0.18 : 0.1;

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(from, ctx.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(to, ctx.currentTime + duration);

    gain.gain.setValueAtTime(Math.max(0.0001, volume) * 0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);

    oscillator.start(ctx.currentTime);
    oscillator.stop(ctx.currentTime + duration);
  } catch {
    /* звук не критичний — мовчимо */
  }
}

const oppKey = (opp: ArbitrageOpportunity) =>
  opp.id ?? `${opp.buyOrder.id}-${opp.sellOrder.id}`;

export function useSpreadAlerts(enabled: boolean) {
  const soundEnabled = useAppStore(state => state.userSettings.soundEnabled);
  const soundVolume = useAppStore(state => state.userSettings.soundVolume);
  const minSpread = useAppStore(state => state.userSettings.minSpread);

  const seen = useRef<Set<string> | null>(null);

  const { data: opportunities } = useSWR<ArbitrageOpportunity[]>(
    enabled ? '/opportunities' : null,
    () => api.getOpportunities(),
    { refreshInterval: 5000, shouldRetryOnError: false }
  );

  // Політика автоплею: контекст, створений без жесту, лишається suspended.
  // Ловимо перший будь-який дотик і відновлюємо його.
  useEffect(() => {
    const resume = () => {
      const ctx = getAudioContext();
      if (ctx?.state === 'suspended') ctx.resume().catch(() => {});
    };
    const events: (keyof WindowEventMap)[] = ['pointerdown', 'keydown'];
    events.forEach(e => window.addEventListener(e, resume, { once: false, passive: true }));
    return () => events.forEach(e => window.removeEventListener(e, resume));
  }, []);

  useEffect(() => {
    if (!opportunities) return;

    const currentKeys = new Set(opportunities.map(oppKey));

    // Перший отриманий список — це не «нові» спреди, а просто те, що вже
    // висіло на момент відкриття сайту. Запам'ятовуємо мовчки.
    if (seen.current === null) {
      seen.current = currentKeys;
      return;
    }

    const fresh = opportunities.filter(opp => !seen.current!.has(oppKey(opp)));
    seen.current = currentKeys;

    if (!fresh.length || soundEnabled === false) return;

    const threshold = minSpread ?? 0;
    const worthy = fresh.filter(opp => opp.netSpread >= threshold);
    if (!worthy.length) return;

    const best = Math.max(...worthy.map(o => o.netSpread));
    playChime(soundVolume ?? 0.5, best >= threshold * 2);
  }, [opportunities, soundEnabled, soundVolume, minSpread]);
}
