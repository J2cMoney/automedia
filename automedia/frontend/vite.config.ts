import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// Vite 8 + React 19 + Tailwind v4 (@tailwindcss/vite 插件,v4 无需 tailwind.config.js)
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,           // 后端 CORS 白名单的端口
    strictPort: true,
    proxy: {
      // 开发态代理 /api 和 /tasks 到后端,避免 CORS(生产部署按需配)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/tasks': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
