import { defineConfig } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
const runtime = mkdtempSync(join(tmpdir(), 'deskmate-e2e-'))
const root = resolve(import.meta.dirname, '..')

export default defineConfig({
  testDir: './tests',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:5184', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: [
    {
      command: '.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8011',
      cwd: root,
      url: 'http://127.0.0.1:8011/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        TEST_MODE: 'true',
        GENERATION_PROVIDER: 'stub',
        EMBED_PROVIDER: 'stub',
        NEO4J_URI: '',
        CASE_DB_PATH: join(runtime, 'cases.db'),
        CHROMA_DIR: join(runtime, 'chroma'),
        ALLOW_URL_INGEST: 'true',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5184 --strictPort',
      cwd: resolve(root, 'frontend'),
      url: 'http://127.0.0.1:5184',
      reuseExistingServer: false,
      env: { API_PROXY_TARGET: 'http://127.0.0.1:8011' },
    },
  ],
})
