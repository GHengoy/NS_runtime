import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    middlewareMode: false,
    // proxy는 config.ts의 동적 감지로 처리 (localhost & 외부IP 모두 지원)
  },
  preview: {
    host: '0.0.0.0',
    port: 5173,
  },
})
