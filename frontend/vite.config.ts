import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      ['/ask', '/requests', '/cases', '/health', '/readiness', '/ingest', '/documents', '/knowledge', '/speech', '/azure', '/auth', '/admin'].map(
        (path) => [
          path,
          { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', ws: true },
        ],
      ),
    ),
  },
  preview: {
    port: 5173,
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
    css: false,
  },
})
