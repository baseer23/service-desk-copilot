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
  expect(screen.getByText(/Document processing is temporarily unavailable/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save document' }))
  expect(await screen.findByText(/Your document is saved/)).toBeInTheDocument()
  expect(fetch.mock.calls.some(([path]) => path === '/documents/paste')).toBe(true)
  expect(fetch.mock.calls.some(([path]) => path.startsWith('/ingest/'))).toBe(false)
})

test('never exposes infrastructure details in processing explanations', () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json([])))
  render(<KnowledgePanel readiness={{ semantic_ingestion_ready: false, semantic_ingestion_blocker: 'Azure text-embedding-3-small TPM quota MCP' } as ReadinessReport} />)
  expect(screen.queryByText(/text-embedding|TPM|MCP|Azure/)).not.toBeInTheDocument()
})

test('never lists or fetches saved documents while preserving upload and search', () => {
  const fetch = vi.fn(async () => Response.json([]))
  vi.stubGlobal('fetch', fetch)
  render(<KnowledgePanel readiness={{ managed_documents: true, semantic_ingestion_ready: true } as ReadinessReport} />)
  expect(screen.queryByText(/Saved documents/i)).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Search document library')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Choose document' })).toBeInTheDocument()
  expect(screen.getByLabelText('Knowledge search')).toBeInTheDocument()
  expect(fetch).not.toHaveBeenCalled()
})
