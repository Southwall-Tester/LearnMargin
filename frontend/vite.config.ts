import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Preserve the browser's Host so the backend's same-origin write check
      // remains valid. Vite's shorthand string proxy changes Host implicitly.
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false },
    },
  },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], restoreMocks: true },
});
