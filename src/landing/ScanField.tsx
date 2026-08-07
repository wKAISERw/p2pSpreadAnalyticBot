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
const FRAG = `precision mediump float;
varying vec2 v_uv;
uniform float u_time;
uniform vec2  u_resolution;
uniform vec2  u_mouse;
uniform vec3  u_accent;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

void main() {
  vec2 uv = v_uv;

  float noise = hash(uv + u_time * 0.01);
  float scan  = smoothstep(0.025, 0.0, abs(uv.y - fract(u_time * 0.14)));

  vec2 cell = fract(uv * vec2(u_resolution.x / 48.0, u_resolution.y / 48.0));
  float grid = smoothstep(0.04, 0.0, cell.x) + smoothstep(0.04, 0.0, cell.y);

  vec2 m = u_mouse / u_resolution;
  float pulse = smoothstep(0.28, 0.0, distance(uv, m));

  float amount = noise * 0.08 + scan * 0.30 + grid * 0.05 + pulse * 0.18;
  gl_FragColor = vec4(u_accent * amount, amount * 0.9);
}`;

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, src);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
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

export default function ScanField({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;

    if (
      window.matchMedia('(prefers-reduced-motion: reduce)').matches ||
      !window.matchMedia('(min-width: 1024px) and (hover: hover) and (pointer: fine)').matches
    ) {
      return;
    }

    const gl = canvas.getContext('webgl', {
      alpha: true,
      antialias: false,
      // Фон не читається назад і не зберігається — дозволяємо драйверу
      // не тримати буфер після кадру.
      preserveDrawingBuffer: false,
      powerPreference: 'low-power',
    });
    if (!gl) return;

    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return;

    const program = gl.createProgram();
    if (!program) return;
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
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

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const uTime = gl.getUniformLocation(program, 'u_time');
    const uRes = gl.getUniformLocation(program, 'u_resolution');
    const uMouse = gl.getUniformLocation(program, 'u_mouse');
    const uAccent = gl.getUniformLocation(program, 'u_accent');

    // Половинна роздільність: це розмитий фон, різниці не видно, а
    // пікселів для зафарбовування вчетверо менше.
    const sync = () => {
      const w = Math.max(1, Math.round(canvas.clientWidth / 2));
      const h = Math.max(1, Math.round(canvas.clientHeight / 2));
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
    window.addEventListener('pointermove', onMove, { passive: true });

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

    const render = (t: number) => {
      frame = requestAnimationFrame(render);
      if (!visible) return;

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
      window.removeEventListener('pointermove', onMove);
      document.removeEventListener('visibilitychange', onVisibility);
      // Явно віддаємо контекст: браузер тримає обмежену кількість
      // живих WebGL-контекстів на вкладку.
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    };
  }, []);

  return <canvas ref={ref} className={className} aria-hidden />;
}
