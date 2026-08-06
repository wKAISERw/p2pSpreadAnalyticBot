/**
 * Кольорова гама інтерфейсу.
 *
 * Задається одним числом — відтінком (hue) в OKLCH. Решта відтінків
 * виводиться з нього формулою, тому будь-який колір із палітри дає
 * узгоджений набір, а не набір випадкових значень.
 *
 * OKLCH обраний свідомо: у ньому однакова світлота виглядає однаково
 * яскравою для ока незалежно від відтінку. У HSL синій на тій самій
 * «яскравості» здається помітно темнішим за зелений, і кнопки в різних
 * гамах виходили б різної ваги.
 */

export interface AccentPreset {
  id: string;
  label: string;
  hue: number;
  /** Для прев'ю в палітрі. */
  swatch: string;
}

export const ACCENT_PRESETS: AccentPreset[] = [
  { id: 'emerald', label: 'Смарагдова', hue: 163, swatch: '#10b981' },
  { id: 'azure', label: 'Синя', hue: 245, swatch: '#4f7cff' },
  { id: 'violet', label: 'Фіолетова', hue: 295, swatch: '#a855f7' },
  { id: 'crimson', label: 'Червона', hue: 22, swatch: '#f43f5e' },
  { id: 'amber', label: 'Помаранчева', hue: 62, swatch: '#f59e0b' },
  { id: 'cyan', label: 'Бірюзова', hue: 205, swatch: '#22d3ee' },
];

export const DEFAULT_HUE = 163;

/** Насиченість трохи падає на світлих і темних краях — так шкала виглядає рівною. */
const STOPS: [name: string, lightness: number, chroma: number][] = [
  ['300', 0.87, 0.14],
  ['400', 0.79, 0.17],
  ['500', 0.7, 0.17],
  ['600', 0.6, 0.15],
];

/**
 * Наближений RGB для випадків, де потрібен саме `rgb(var(--accent-rgb))`
 * — наприклад, у keyframes із прозорістю. Точність тут не критична:
 * значення йде лише у ледь помітний підсвіт.
 */
function oklchToRgb(l: number, c: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180;
  const a = c * Math.cos(h);
  const bb = c * Math.sin(h);

  const l_ = l + 0.3963377774 * a + 0.2158037573 * bb;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * bb;
  const s_ = l - 0.0894841775 * a - 1.291485548 * bb;

  const lc = l_ ** 3;
  const mc = m_ ** 3;
  const sc = s_ ** 3;

  const rgb = [
    +4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
    -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
    -0.0041960863 * lc - 0.7034186147 * mc + 1.707614701 * sc,
  ];

  return rgb.map(v => {
    const srgb = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
    return Math.round(Math.min(1, Math.max(0, srgb)) * 255);
  }) as [number, number, number];
}

let switchTimer: ReturnType<typeof setTimeout> | undefined;

/** Застосовує гаму до документа. Дешево: чотири CSS-змінні на :root. */
export function applyAccent(hue: number, animated = true): void {
  if (typeof document === 'undefined') return;

  const root = document.documentElement;

  if (animated) {
    // Клас вмикає плавний перехід кольорів і знімається після нього —
    // тримати transition на всьому документі постійно означало б
    // сповільнювати кожен ховер і кожну зміну стану.
    root.classList.add('accent-switching');
    clearTimeout(switchTimer);
    switchTimer = setTimeout(() => root.classList.remove('accent-switching'), 260);
  }

  for (const [name, lightness, chroma] of STOPS) {
    root.style.setProperty(`--accent-${name}`, `oklch(${lightness} ${chroma} ${hue})`);
  }

  const [r, g, b] = oklchToRgb(0.7, 0.17, hue);
  root.style.setProperty('--accent-rgb', `${r} ${g} ${b}`);
}

/** Відтінок за id пресета або числом, збереженим у налаштуваннях. */
export function resolveHue(accent: string | number | undefined): number {
  if (typeof accent === 'number' && Number.isFinite(accent)) return accent;
  if (typeof accent === 'string') {
    const preset = ACCENT_PRESETS.find(p => p.id === accent);
    if (preset) return preset.hue;

    const parsed = Number(accent);
    if (Number.isFinite(parsed)) return parsed;
  }
  return DEFAULT_HUE;
}
