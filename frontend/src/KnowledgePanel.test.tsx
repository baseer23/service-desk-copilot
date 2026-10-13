import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import KnowledgePanel from './KnowledgePanel'
import { type ReadinessReport } from './readiness'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('keeps local saving usable and explains disabled indexing', async () => {
  const fetch = vi.fn(async (path: string) =>
    Response.json(path === '/documents' ? [] : { chunks: 1, storage: 'local', indexed: false }),
  )
  vi.stubGlobal('fetch', fetch)
  render(
    <KnowledgePanel
      readiness={
        {
          semantic_ingestion_ready: false,
          semantic_ingestion_blocker: 'Azure embeddings are disabled.',
        } as ReadinessReport
      }
    />,
  )
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Knowledge text'), 'Fictional VPN guidance')
  expect(screen.getByRole('button', { name: 'Add to knowledge' })).toBeDisabled()
  expect(screen.getByText('Azure embeddings are disabled.')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save document locally' }))
  expect(await screen.findByText(/chunks saved locally/)).toBeInTheDocument()
  expect(fetch.mock.calls.some(([path]) => path === '/documents/paste')).toBe(true)
  expect(fetch.mock.calls.some(([path]) => path.startsWith('/ingest/'))).toBe(false)
})
