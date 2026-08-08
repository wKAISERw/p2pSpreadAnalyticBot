/**
 * Чи це пристрій, на якому має сенс диплінк у застосунок біржі.
 *
 * Перевіряємо саме тип пристрою, а не ширину екрана: вузьке вікно на
 * десктопі — це все одно десктоп, і кнопка «Відкрити в застосунку» там веде
 * на сторінку завантаження мобільного додатка. Тобто медіазапит дав би
 * кнопку, яка обіцяє одне, а робить інше.
 *
 * maxTouchPoints — найнадійніший сигнал у сучасних браузерах; userAgent
 * лишається запасним для старіших. Обидва перевіряються один раз: тип
 * пристрою не змінюється поки відкрита вкладка.
 */
let cached: boolean | null = null;

export function isMobileDevice(): boolean {
  if (cached !== null) return cached;
  if (typeof window === 'undefined' || typeof navigator === 'undefined') {
    cached = false;
    return cached;
  }

  const touch = (navigator.maxTouchPoints ?? 0) > 0;
  const mobileUa = /android|iphone|ipad|ipod|windows phone/i.test(navigator.userAgent);

  // iPad із iPadOS 13+ представляється як Mac, тому дотик тут вирішальний.
  cached = touch && (mobileUa || /macintosh/i.test(navigator.userAgent));
  return cached;
}
