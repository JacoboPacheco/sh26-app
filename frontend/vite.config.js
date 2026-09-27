import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// VITE_PROXY_TARGET lets a second dev server point at a second backend (parallel work on one machine).
const target = process.env.VITE_PROXY_TARGET || 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': target,
    },
  },
})
