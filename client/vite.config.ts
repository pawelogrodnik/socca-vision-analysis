import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), '');
  const backendTarget = environment.VITE_DEV_BACKEND_URL || 'http://localhost:8000';
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': backendTarget,
        '/health': backendTarget,
      },
    },
  };
});
