import React from 'react';

/**
 * Фонове поле публічних сторінок.
 *
 * Три кольорові плями повільно розходяться, і весь набір зсувається при
 * скролі — виходить м'який перелив із паралаксом.
 *
 * Три рішення, які тут важливі:
 *
 *  1. Немає filter: blur(). Розмиття на елементі в пів-екрана браузер
 *     перераховує щокадру й тримає окремий буфер у GPU. Плями зроблені
 *     radial-gradient із розтягнутими стопами — м'якість та сама, ціна
 *     майже нульова.
 *  2. Паралакс на CSS animation-timeline: scroll(), а не на слухачі
 *     скролу. Це рахує композитор; JS у кадрі не бере участі взагалі.
 *     Де не підтримується — лишається дрейф, і сторінка нічого не втрачає.
 *  3. contain: strict на контейнері — браузер знає, що всередині ніщо не
 *     впливає на решту сторінки, і не перевіряє це при кожній зміні.
 *
 * На дашборд це свідомо не ставиться: там живі цифри, і фон, що рухається
 * за ними, заважає читати.
 */
export default function AuroraField() {
  return (
    <div className="aurora-field" aria-hidden>
      <div className="aurora-parallax">
        <div className="aurora-blob aurora-blob-1" />
        <div className="aurora-blob aurora-blob-2" />
        <div className="aurora-blob aurora-blob-3" />
      </div>
      <div className="aurora-grid" />
    </div>
  );
}
