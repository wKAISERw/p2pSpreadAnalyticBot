import React from 'react';
import { motion } from 'motion/react';
import { Palette, Check } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useAppStore } from '../../store';
import { ACCENT_PRESETS, applyAccent, resolveHue } from '../../lib/theme';

/**
 * Кольорова гама інтерфейсу.
 *
 * Зберігається як число — відтінок в OKLCH. Пресети це просто іменовані
 * значення того самого числа, тож повзунок і кнопки не конфліктують:
 * обрав пресет — повзунок став на його позицію, посунув повзунок — жоден
 * пресет не підсвічений.
 */
export default function AppearanceSection() {
  const userSettings = useAppStore(state => state.userSettings);
  const setUserSettings = useAppStore(state => state.setUserSettings);

  const hue = resolveHue(userSettings.accentColor);

  const pick = (nextHue: number) => {
    applyAccent(nextHue);
    setUserSettings({ ...userSettings, accentColor: nextHue });
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex items-center gap-3 mb-5">
        <div className="p-2 bg-slate-800/60 rounded-xl">
          <Palette className="w-5 h-5 text-accent-400" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-white">Кольорова гама</h2>
          <p className="text-xs text-slate-400">Акцент інтерфейсу — застосовується одразу</p>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-6">
        {ACCENT_PRESETS.map(preset => {
          const active = Math.abs(hue - preset.hue) < 1;
          return (
            <button
              key={preset.id}
              onClick={() => pick(preset.hue)}
              className={cn(
                'flex items-center gap-3 p-3 rounded-2xl border transition-all',
                active
                  ? 'bg-slate-950 border-accent-500/40'
                  : 'bg-slate-950/50 border-slate-800 hover:border-slate-700'
              )}
            >
              <span
                className="w-8 h-8 rounded-xl shrink-0 flex items-center justify-center"
                style={{ backgroundColor: preset.swatch }}
              >
                {active && <Check className="w-4 h-4 text-slate-950" strokeWidth={3} />}
              </span>
              <span className={cn('text-xs font-bold', active ? 'text-white' : 'text-slate-400')}>
                {preset.label}
              </span>
            </button>
          );
        })}
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-bold text-white">Свій відтінок</span>
          <span className="text-xs text-slate-500 tabular-nums">{Math.round(hue)}°</span>
        </div>

        <input
          type="range"
          min={0}
          max={360}
          value={hue}
          onChange={e => pick(Number(e.target.value))}
          className="w-full h-2 rounded-full appearance-none cursor-pointer accent-accent-500"
          style={{
            // Повний круг відтінків при тій самій світлоті, що й акцент —
            // видно рівно ті кольори, які реально отримаєш.
            background:
              'linear-gradient(to right,' +
              Array.from({ length: 13 }, (_, i) => `oklch(0.7 0.17 ${i * 30})`).join(',') +
              ')',
          }}
        />

        <div className="flex items-center gap-3 mt-4">
          <div className="flex-1 h-10 rounded-xl bg-accent-500" />
          <div className="flex-1 h-10 rounded-xl bg-accent-500/20 border border-accent-500/40" />
          <button className="px-4 py-2 rounded-xl bg-accent-500 text-slate-950 text-xs font-bold shrink-0">
            Кнопка
          </button>
        </div>
      </div>
    </motion.section>
  );
}
