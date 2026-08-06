import { useEffect, useRef } from 'react';
import { firestoreDoc } from '../lib/google';
import { useAppStore } from '../store';

/**
 * Синхронізація налаштувань САЙТУ між браузерами й пристроями.
 *
 * Це і є відповідь на питання «навіщо тут Google». Особу визначає Telegram,
 * і все, що стосується грошей — фільтри, картки, ключі бірж — живе в боті.
 * Але є речі, яких бот не знає і знати не має: звук алертів, гучність,
 * акцентний колір, ціль по капіталу, обраний режим і приховані біржі.
 *
 * Раніше вони лежали лише в localStorage, тобто помирали разом із
 * браузером і не переїжджали на інший пристрій. Прив'язаний Google робить
 * їх спільними для всіх твоїх сесій.
 *
 * Документ адресується telegram_id, а не Google-uid: якщо колись
 * перепідв'яжеш інший Google, налаштування лишаться при тобі.
 */

const DEBOUNCE_MS = 800;

interface CloudPrefs {
  soundEnabled?: boolean;
  soundVolume?: number;
  accentColor?: number | string;
  goalCapital?: number;
  tradingMode?: 'taker' | 'maker';
  excludedExchanges?: string[];
}

export function useCloudPrefs(hasGoogleLink: boolean) {
  const telegramId = useAppStore(state => state.auth?.telegramId);
  const userSettings = useAppStore(state => state.userSettings);
  const tradingMode = useAppStore(state => state.tradingMode);
  const excludedExchanges = useAppStore(state => state.excludedExchanges);

  // Прапорець «зміна прийшла з хмари» — без нього snapshot одразу тригерив
  // би зворотний запис, і два відкриті таби ганяли б апдейти по колу.
  const applyingRemote = useRef(false);
  const isFirstPush = useRef(true);

  // PULL
  useEffect(() => {
    if (!telegramId || !hasGoogleLink) return;

    let unsubscribe: (() => void) | undefined;
    let cancelled = false;

    (async () => {
      const [ref, { onSnapshot }] = await Promise.all([
        firestoreDoc('webPrefs', String(telegramId)),
        import('firebase/firestore'),
      ]);
      if (cancelled) return;

      unsubscribe = onSnapshot(ref, snapshot => {
      if (!snapshot.exists()) return;
      const remote = snapshot.data() as CloudPrefs;
      const store = useAppStore.getState();

      applyingRemote.current = true;

      const merged = { ...store.userSettings };
      let touched = false;
      for (const key of ['soundEnabled', 'soundVolume', 'accentColor', 'goalCapital'] as const) {
        if (remote[key] !== undefined && remote[key] !== merged[key]) {
          (merged as any)[key] = remote[key];
          touched = true;
        }
      }
      if (touched) store.setUserSettings(merged);

      if (remote.tradingMode && remote.tradingMode !== store.tradingMode) {
        store.setTradingMode(remote.tradingMode);
      }
      if (
        remote.excludedExchanges &&
        JSON.stringify(remote.excludedExchanges) !== JSON.stringify(store.excludedExchanges)
      ) {
        store.setExcludedExchanges(remote.excludedExchanges);
      }

        applyingRemote.current = false;
      });
    })();

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [telegramId, hasGoogleLink]);

  // PUSH
  useEffect(() => {
    if (!telegramId || !hasGoogleLink) return;
    if (applyingRemote.current) return;

    // Перший прогін — це просто підняття стану після завантаження;
    // писати його в хмару означало б затерти свіжіші дані з іншого пристрою.
    if (isFirstPush.current) {
      isFirstPush.current = false;
      return;
    }

    const timeout = setTimeout(async () => {
      const payload: CloudPrefs = {
        soundEnabled: userSettings.soundEnabled,
        soundVolume: userSettings.soundVolume,
        accentColor: userSettings.accentColor,
        goalCapital: userSettings.goalCapital,
        tradingMode,
        excludedExchanges,
      };
      try {
        const [ref, { setDoc }] = await Promise.all([
          firestoreDoc('webPrefs', String(telegramId)),
          import('firebase/firestore'),
        ]);
        await setDoc(ref, payload, { merge: true });
      } catch {
        /* офлайн — синхронізуємось наступного разу */
      }
    }, DEBOUNCE_MS);

    return () => clearTimeout(timeout);
  }, [
    telegramId,
    hasGoogleLink,
    userSettings.soundEnabled,
    userSettings.soundVolume,
    userSettings.accentColor,
    userSettings.goalCapital,
    tradingMode,
    excludedExchanges,
  ]);
}
