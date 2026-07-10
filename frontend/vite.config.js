import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 개발 시: vite(5173) → FastAPI(8007) 프록시
// 배포 시: npm run build → FastAPI가 frontend/dist 를 직접 서빙
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': 'http://localhost:8007',
    },
  },
})
