import React, { useEffect, useRef } from 'react';

/**
 * Фон зі скануючою лінією на WebGL.
 *
 * Взято з референсу редизайну: шум, промінь, що йде згори вниз, сітка й
 * пульс за курсором. Це єдиний фрагментний шейдер без жодної бібліотеки
 * — приблизно два кілобайти коду проти сотень, які коштувала б сцена на
 * three.js заради того самого відчуття.
 *
 * Гейтингу тут більше, ніж самого шейдера, і це навмисно. Фонова
 * анімація крутить requestAnimationFrame весь час, поки відкрита
 * сторінка, тож вона вимикається скрізь, де ціна перевищує користь:
 *
 *   • prefers-reduced-motion — рух може викликати нудоту, це не декор;
 *   • вузький екран або відсутність точного курсора — на телефоні це
 *     насамперед витрата батареї, а половина ефекту (пульс за мишею)
 *     там взагалі не працює;
 *   • прихована вкладка — інакше GPU гріється у фоні;
 *   • немає WebGL — тихо нічого не малюємо, у секції лишається її
 *     звичайний фон.
 */

/*
 * highp там, де він є, з відкатом на mediump.
 *
 * У mediump діапазон float близько ±65504 з десятком біт мантиси, а
 * класичний хеш-трюк множить синус на 43758.5453 — тобто вилітає за
 * точність і дає сміття, іноді NaN. NaN у gl_FragColor це невизначена
 * поведінка: на одній відеокарті виходить нуль, на іншій біла заливка на
 * весь шар.
 *
 * Пояснення живе тут, а не в тексті шейдера, і це принципово: GLSL ES
 * 1.00 допускає обмежений набір символів, і частина драйверів відхиляє
 * не-ASCII навіть усередині коментарів. Кирилиця в шейдері — це той
 * самий клас помилок «залежить від відеокарти», який ми щойно ловили.
 */
const VERT = `attribute vec2 a_position;
varying vec2 v_uv;
void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

/*
 * u_accent приходить ззовні, щоб фон слухався вибраного кольору теми —
 * у референсі колір був зашитий, і при зміні акценту сторінка
 * розповзалась на два різних зелених.
 */
const FRAG = `#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 v_uv;
uniform float u_time;
uniform vec2  u_resolution;
uniform vec2  u_mouse;
uniform vec3  u_accent;

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(12.9898, 78.233))) * 137.51);
}

void main() {
  vec2 uv = v_uv;

  float noise = hash(uv + u_time * 0.01);
  float scan  = smoothstep(0.025, 0.0, abs(uv.y - fract(u_time * 0.14)));

  vec2 cell = fract(uv * vec2(u_resolution.x / 48.0, u_resolution.y / 48.0));
  float grid = smoothstep(0.04, 0.0, cell.x) + smoothstep(0.04, 0.0, cell.y);

  vec2 m = u_mouse / u_resolution;
  float pulse = smoothstep(0.28, 0.0, distance(uv, m));

  float a = clamp(noise * 0.05 + scan * 0.34 + grid * 0.06 + pulse * 0.20, 0.0, 1.0);
  gl_FragColor = vec4(u_accent * a, a);
}`;

/*
 * Другий варіант — потік даних, що стікає донизу.
 *
 * Той самий кістяк, інший малюнок: вертикальні доріжки різної швидкості
 * плюс рідкі яскраві «пакети», що падають ними. Скануючий промінь
 * підходив сторінці про перевірку; тут потрібне відчуття безперервного
 * обходу майданчиків, а не одноразового просвічування.
 */
/*
 * Стик між станами доріжок.
 *
 * Було: alive = step(порiг, hash(lane + floor(time * 0.25) * 13.0)).
 * floor() робить час східчастим, тож раз на чотири секунди ВСІ доріжки
 * одночасно перемикались - падаючі прямокутники стрибали з одного
 * положення в інше. Саме це й читалось як зациклений короткий ролик без
 * м'якого стику.
 *
 * Стало: беремо стан поточного кроку й наступного і переливаємо між ними
 * за дробовою частиною того самого часу. Перемикання лишається, але воно
 * розмазане по всьому інтервалу, а не стається в один кадр.
 */
const FRAG_FLOW = `#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 v_uv;
uniform float u_time;
uniform vec2  u_resolution;
uniform vec2  u_mouse;
uniform vec3  u_accent;

float hash(float x) { return fract(sin(x * 12.9898) * 137.51); }

void main() {
  vec2 uv = v_uv;

  float lane = floor(uv.x * u_resolution.x / 40.0);
  float speed = 0.10 + hash(lane) * 0.35;
  float phase = hash(lane + 7.0);

  float head = fract(uv.y + u_time * speed + phase);
  float packet = pow(1.0 - head, 26.0);

  float slot = floor(u_time * 0.25);
  float k = fract(u_time * 0.25);
  float a0 = step(0.55, hash(lane + slot * 13.0));
  float a1 = step(0.55, hash(lane + (slot + 1.0) * 13.0));
  float alive = mix(a0, a1, smoothstep(0.0, 1.0, k));

  float grid = smoothstep(0.035, 0.0, fract(uv.x * u_resolution.x / 40.0));

  vec2 m = u_mouse / u_resolution;
  float pulse = smoothstep(0.3, 0.0, distance(uv, m));

  float a = clamp(packet * alive * 0.6 + grid * 0.05 + pulse * 0.14, 0.0, 1.0);
  gl_FragColor = vec4(u_accent * a, a);
}`;

/*
 * Третій варіант - сітка вузлів, що дихає.
 *
 * Точки на регулярній решітці повільно пульсують хвилею, яка йде по
 * діагоналі, плюс кільце розходиться від курсора. Ні різких перемикань,
 * ні квантованого часу: усе крутиться на синусах, тож циклу як такого не
 * видно взагалі - саме те, чого бракувало потоку даних.
 */
const FRAG_MESH = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
varying vec2 v_uv;
uniform float u_time;
uniform vec2  u_resolution;
uniform vec2  u_mouse;
uniform vec3  u_accent;

void main() {
  vec2 uv = v_uv;
  vec2 px = uv * u_resolution;

  float step_px = 34.0;
  vec2 cell = mod(px, step_px) - step_px * 0.5;
  vec2 id = floor(px / step_px);

  float d = length(cell);

  float wave = sin((id.x + id.y) * 0.35 - u_time * 0.9) * 0.5 + 0.5;
  float radius = 1.1 + wave * 1.9;
  float dot_a = smoothstep(radius, radius - 1.2, d) * (0.10 + wave * 0.22);

  vec2 m = u_mouse / u_resolution;
  float md = distance(uv, m);
  float ring = smoothstep(0.03, 0.0, abs(md - fract(u_time * 0.18) * 0.45));
  float near = smoothstep(0.34, 0.0, md);

  float a = clamp(dot_a + ring * near * 0.30 + near * 0.05, 0.0, 1.0);
  gl_FragColor = vec4(u_accent * a, a);
}`;

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, src);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    // Мовчазна невдача тут коштувала кількох невірних діагнозів: шейдер
    // не збирався, а сторінка про це ніяк не повідомляла.
    console.warn('[ScanField] шейдер не зібрався:', gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/** Поточний акцент теми як 0..1 RGB — змінна зберігає «16 185 129». */
function readAccent(): [number, number, number] {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue('--accent-rgb')
    .trim();
  const parts = raw.split(/[\s,]+/).map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return [0.06, 0.72, 0.51];
  return [parts[0] / 255, parts[1] / 255, parts[2] / 255];
}

export default function ScanField({
  className,
  variant = 'scan',
}: {
  className?: string;
  variant?: 'scan' | 'flow' | 'mesh';
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    // Єдина умова, за якої фон не малюється взагалі, — вимкнений рух.
    // Раніше тут відсікались ще й телефони: фонова анімація там коштує
    // батареї. Але вимикати ефект цілком заради цього — надто грубо, тож
    // на дотикових пристроях він тепер просто дешевший (див. нижче).
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const coarse = window.matchMedia('(pointer: coarse)').matches;

    /*
     * Стартова якість.
     *
     * Характеристики дають лише підказку, з чого почати: ядра й пам'ять
     * корелюють зі швидкістю GPU погано, і покладатись на них як на
     * вирок не можна. Тому вони тільки обирають початкову сходинку, а
     * далі рішення приймають виміряні кадри.
     *
     * QUALITY[0] — десктоп, [1] — телефон, [2] — слабкий пристрій.
     * Далі цього — вимкнення.
     */
    const QUALITY = [
      { scale: 2, minFrameMs: 0 },
      { scale: 4, minFrameMs: 33 },
      { scale: 6, minFrameMs: 50 },
    ];

    const cores = navigator.hardwareConcurrency ?? 4;
    const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 4;
    let tier = coarse ? 1 : 0;
    if (coarse && (cores <= 4 || memory <= 2)) tier = 2;

    const lite = coarse;

    const gl = canvas.getContext('webgl', {
      alpha: true,
      antialias: false,
      // Фон не читається назад і не зберігається — дозволяємо драйверу
      // не тримати буфер після кадру.
      preserveDrawingBuffer: false,
      powerPreference: 'low-power',
    });
    if (!gl) return;

    // Підстраховка: якщо контекст усе ж загублено (переповнення ліміту
    // вкладки, скидання драйвера), малювати нічого — і про це має бути
    // видно, а не тиша.
    if (gl.isContextLost()) {
      console.warn('[ScanField] контекст втрачено, фон не малюється');
      return;
    }

    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, variant === 'flow' ? FRAG_FLOW : variant === 'mesh' ? FRAG_MESH : FRAG);
    if (!vs || !fs) return;

    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn('[ScanField] програма не злінкувалась:', gl.getProgramInfoLog(program));
      return;
    }
    gl.useProgram(program);

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
      gl.STATIC_DRAW
    );
    const aPos = gl.getAttribLocation(program, 'a_position');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    gl.disable(gl.BLEND);

    const uTime = gl.getUniformLocation(program, 'u_time');
    const uRes = gl.getUniformLocation(program, 'u_resolution');
    const uMouse = gl.getUniformLocation(program, 'u_mouse');
    const uAccent = gl.getUniformLocation(program, 'u_accent');

    // Невстановлена уніформа лишається нулем — тобто чорним кольором на
    // весь екран. Краще не малювати взагалі, ніж малювати навмання.
    if (!uTime || !uRes || !uMouse || !uAccent) return;

    // Чистимо одразу: до першого кадру буфер містить те, що лишив
    // драйвер, і композитор може встигнути показати цей сміттєвий вміст.
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    // Понижена роздільність: це розмитий фон, різниці не видно, а
    // пікселів для зафарбовування менше в рази. На телефоні ділимо на
    // чотири — там і екран щільніший, і GPU слабший.
    const sync = () => {
      const scale = QUALITY[tier].scale;
      const w = Math.max(1, Math.round(canvas.clientWidth / scale));
      const h = Math.max(1, Math.round(canvas.clientHeight / scale));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
    };
    sync();

    const ro = new ResizeObserver(sync);
    ro.observe(canvas);

    const mouse = { x: 0.5, y: 0.5 };
    const onMove = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      mouse.x = (e.clientX - rect.left) / rect.width;
      mouse.y = 1 - (e.clientY - rect.top) / rect.height;
    };
    // Пульс за курсором існує лише там, де курсор є.
    if (!lite) window.addEventListener('pointermove', onMove, { passive: true });

    let accent = readAccent();
    // Зміна теми пише --accent-rgb на <html>: перечитуємо звідти, а не
    // тримаємо колір у стані React.
    const themeObserver = new MutationObserver(() => {
      accent = readAccent();
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['style', 'data-theme'],
    });

    let frame = 0;
    let visible = !document.hidden;

    let lastDraw = 0;

    /*
     * Самоналаштування за фактом, а не за паспортом.
     *
     * Рахуємо, скільки часу пішло на кожен намальований кадр. Якщо
     * протягом вибірки ми стабільно не вкладаємось у бюджет — знижуємо
     * сходинку якості. Коли знижувати вже нікуди, фон вимикається
     * зовсім: на дуже слабкому пристрої він не вартий того, щоб через
     * нього гальмувала прокрутка.
     *
     * Вибірка починається заново після кожного зниження, щоб рішення
     * приймалось уже за новою якістю, а не за старими вимірами.
     */
    const SAMPLE = 45;
    let samples = 0;
    let slowFrames = 0;

    const judge = (drawMs: number) => {
      // Бюджет: третина інтервалу між кадрами. Більше означає, що фон
      // з'їдає час, потрібний прокрутці й анімаціям сторінки.
      const budget = QUALITY[tier].minFrameMs ? QUALITY[tier].minFrameMs / 3 : 5;
      if (drawMs > budget) slowFrames++;
      if (++samples < SAMPLE) return;

      const slowShare = slowFrames / samples;
      samples = 0;
      slowFrames = 0;
      if (slowShare < 0.35) return;

      if (tier >= QUALITY.length - 1) {
        // Нижче вже нікуди — прибираємо фон і перестаємо малювати.
        cancelAnimationFrame(frame);
        frame = 0;
        canvas.style.display = 'none';
        return;
      }
      tier++;
      sync();
    };

    const render = (t: number) => {
      frame = requestAnimationFrame(render);
      if (!visible) return;
      if (t - lastDraw < QUALITY[tier].minFrameMs) return;
      lastDraw = t;

      const drawStart = performance.now();

      gl.viewport(0, 0, canvas.width, canvas.height);

      // Без цього кадр домальовується поверх попереднього: блендинг
      // увімкнений, тож напівпрозорий шар накладається сам на себе
      // сотні разів за секунду й за кілька секунд вироджується в суцільну
      // світлу пелену на пів-екрана. Полотно треба чистити щокадру.
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);

      gl.uniform1f(uTime, t * 0.001);
      gl.uniform2f(uRes, canvas.width, canvas.height);
      gl.uniform2f(uMouse, mouse.x * canvas.width, mouse.y * canvas.height);
      gl.uniform3f(uAccent, accent[0], accent[1], accent[2]);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

      judge(performance.now() - drawStart);
    };

    const onVisibility = () => {
      visible = !document.hidden;
    };
    document.addEventListener('visibilitychange', onVisibility);

    frame = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      themeObserver.disconnect();
      if (!lite) window.removeEventListener('pointermove', onMove);
      document.removeEventListener('visibilitychange', onVisibility);
      // Контекст НЕ вбиваємо. Спокуса була саме така — мовляв, браузер
      // тримає обмежену кількість живих контекстів на вкладку, тож
      // віддамо свій явно. Але полотно має рівно один контекст на весь
      // свій вік: після loseContext() повторний getContext() повертає
      // той самий, уже мертвий. А ефект перезапускається легко —
      // подвійне монтування в StrictMode, HMR, зміна variant.
      //
      // Наслідок був такий: перше монтування малює, cleanup убиває
      // контекст, друге отримує труп — і далі мовчки не працює нічого.
      // Шейдери «не збираються» з порожнім логом, полотно лишається
      // невизначеним, а що саме покаже композитор — залежить від
      // драйвера. Звідси й те, що в мене виходила порожнеча, а в
      // користувача світла пелена.
      //
      // Контекст і так звільниться разом із полотном при розмонтуванні.
    };
  }, [variant]);

  return <canvas ref={ref} className={className} aria-hidden />;
}
