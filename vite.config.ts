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
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            firebase: ['firebase/app', 'firebase/auth', 'firebase/firestore'],
            motion: ['motion/react'],
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
