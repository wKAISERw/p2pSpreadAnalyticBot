import React from 'react';
import { motion } from 'motion/react';
import { LayoutGrid, Info, RotateCcw } from 'lucide-react';
import { cn } from '../../lib/utils';
import { useAppStore } from '../../store';
import {
  ORDER_CARD_DEFAULTS, ORDER_CARD_LABELS, OrderCardFields,
  countHeavyFields, resolveOrderCardFields,
} from '../../lib/orderCard';

/**
 * Наповнення картки ордера на дашборді.
 *
 * Це не те саме, що «Вивід алертів» сусідньою вкладкою. Там налаштовується
 * повідомлення в Telegram — його читають поодинці, і довгий текст умов чи
 * розбір LLM там доречні. Тут картки стоять сіткою по кілька десятків, і ті
 * самі поля перетворюють список на суцільний текст, крізь який не видно
 * головного — цін і лімітів.
 *
 * Тому набір окремий, живе в браузері (і їде в хмару, якщо прив'язаний
 * Google), а бота не стосується взагалі.
 */
export default function OrderCardSection() {
  const userSettings = useAppStore(state => state.userSettings);
  const setUserSettings = useAppStore(state => state.setUserSettings);

  const fields = resolveOrderCardFields(userSettings.orderCard);
  const heavy = countHeavyFields(fields);

  const toggle = (key: keyof OrderCardFields) =>
    setUserSettings({
      ...userSettings,
      orderCard: { ...fields, [key]: !fields[key] },
    });

  const reset = () => setUserSettings({ ...userSettings, orderCard: undefined });

  const isDefault = ORDER_CARD_LABELS.every(
    ({ key }) => fields[key] === ORDER_CARD_DEFAULTS[key]
  );

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-slate-900/50 border border-slate-800/50 rounded-3xl p-6"
    >
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-slate-800/60 rounded-xl">
            <LayoutGrid className="w-5 h-5 text-accent-400" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Картка ордера</h2>
            <p className="text-xs text-slate-400">
              Що показувати в списках «Купівля» і «Продаж» на дашборді
            </p>
          </div>
        </div>

        <button
          onClick={reset}
          disabled={isDefault}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 disabled:opacity-40 text-slate-300 text-xs font-bold transition-colors shrink-0"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          Скинути
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {ORDER_CARD_LABELS.map(({ key, title, hint }) => {
          const on = fields[key];
          return (
            <button
              key={key}
              onClick={() => toggle(key)}
              className={cn(
                'flex items-start gap-3 p-4 rounded-2xl border text-left transition-all',
                on ? 'bg-accent-500/5 border-accent-500/25' : 'bg-slate-950/50 border-slate-800'
              )}
            >
              <span className={cn(
                'w-9 h-5 rounded-full relative transition-colors shrink-0 mt-0.5',
                on ? 'bg-accent-500' : 'bg-slate-700'
              )}>
                <span className={cn(
                  'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all',
                  on ? 'right-0.5' : 'left-0.5'
                )} />
              </span>
              <span className="min-w-0">
                <span className={cn('block text-sm font-bold', on ? 'text-accent-400' : 'text-slate-300')}>
                  {title}
                </span>
                <span className="block text-[11px] text-slate-500 leading-snug">{hint}</span>
              </span>
            </button>
          );
        })}
      </div>

      {/* Чесне попередження про висоту: чотири метрики в рядку — це вже
          окремий блок під шапкою картки, а разом з умовами угоди на екран
          влазить утричі менше ордерів. */}
      {(heavy >= 4 || fields.terms) && (
        <div className="flex items-start gap-2 mt-4 px-4 py-3 rounded-2xl bg-orange-500/5 border border-orange-500/20">
          <Info className="w-3.5 h-3.5 text-orange-400 shrink-0 mt-0.5" />
          <span className="text-[11px] text-orange-300/90 leading-snug">
            {fields.terms
              ? 'Умови угоди займають до трьох рядків тексту — картки стануть помітно вищими, і на екран поміститься менше ордерів.'
              : 'Увімкнено всі чотири метрики — картки виростуть на цілий блок.'}
          </span>
        </div>
      )}

      <div className="flex items-start gap-2 mt-4 text-[11px] text-slate-500 leading-snug">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Стосується лише вигляду сайту. Те, що приходить у Telegram,
          налаштовується у вкладці «Сповіщення» і зберігається в боті.
        </span>
      </div>
    </motion.section>
  );
}
