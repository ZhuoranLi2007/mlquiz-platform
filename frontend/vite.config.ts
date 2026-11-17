import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': { target: loadEnv(mode, '.', 'MLQ_').MLQ_DEV_API_URL || 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
}))
