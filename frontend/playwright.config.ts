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
        DATA_PLATFORM: 'local',
        LOCAL_PERSISTENCE_ENABLED: 'false',
        LOCAL_DOCUMENTS_DIR: join(runtime, 'documents'),
        DEPLOYMENT_MODE: 'local',
        ENABLE_LOCAL_FIXTURES: 'true',
        GENERATION_PROVIDER: 'stub',
        KNOWLEDGE_SCOPE: 'support',
        RESEARCH_SOURCE_POLICY: 'prefer_primary',
        MAX_INVESTIGATION_CYCLES: '8',
        // Research routing has backend tests with explicit external-service mocks.
        // Public crawling here would persist nondeterministic pages into this suite's KB.
        MAX_RESEARCH_ROUNDS: '0',
        EMBED_PROVIDER: 'stub',
        NEO4J_URI: '',
        CASE_DB_PATH: join(runtime, 'cases.db'),
        CHROMA_DIR: join(runtime, 'chroma'),
        ALLOW_URL_INGEST: 'true',
        SPEECH_ENABLED: 'false',
        AZURE_SPEECH_ENDPOINT: '',
        VOICE_SESSION_SIGNING_KEY: 'e2e-isolated-signing-key-xxxxxxxxxxxxxxxxxxxxxxxxxx',
        ALLOWED_ORIGINS: 'http://127.0.0.1:5184',
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
