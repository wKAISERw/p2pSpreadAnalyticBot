import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig, loadEnv} from 'vite';

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react(), tailwindcss()],
    define: {
      'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY),
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    build: {
      rollupOptions: {
        output: {
          // Важкі бібліотеки — окремими чанками з довгим кешем: вони
          // змінюються рідше за код застосунку, тож переживають деплої.
          // recharts свідомо НЕ виносимо: він потрібен лише «Аналітиці»,
          // і в ручному чанку Vite додає на нього modulepreload у entry —
          // тобто 377 кБ їхали б кожному, хто просто відкрив дашборд.
          // Залишений усередині лінивого чанка панелі він вантажиться
          // рівно тоді, коли туди заходять.
          /*
           * motion сюди НЕ виноситься навмисно.
           *
           * Окремий ручний чанк змушує Vite прописати на нього
           * modulepreload у входовому HTML — тобто 128 кБ їдуть кожному,
           * хто просто відкрив головну. А framer-motion потрібен лише
           * сторінці входу й панелям дашборду, і обидва — ліниві
           * маршрути. Лишений у їхніх чанках, він завантажується тоді,
           * коли справді потрібен.
           *
           * Та сама пастка вже спрацьовувала тут із recharts.
           */
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            firebase: ['firebase/app', 'firebase/auth', 'firebase/firestore'],
          },
        },
      },
    },
    server: {
      // HMR is disabled in AI Studio via DISABLE_HMR .env var.
      // Do not modifyâfile watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
    },
  };
});
