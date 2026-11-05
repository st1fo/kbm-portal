import { defineConfig } from 'vite';

export default defineConfig({base:'/vehicle-logbook/',
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
});
