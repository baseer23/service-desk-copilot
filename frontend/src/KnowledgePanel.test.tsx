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
  expect(screen.queryByText(/text-embedding|TPM|MCP/)).not.toBeInTheDocument()
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

test('hybrid policy is always on and applies to the selected upload', async () => {
  const fetch = vi.fn(async () => Response.json({ chunks: 1, status: 'queued' }))
  vi.stubGlobal('fetch', fetch)
  render(
    <KnowledgePanel
      readiness={{
        managed_documents: true,
        semantic_ingestion_ready: true,
        azure_document_intelligence_available: true,
        neo4j_read_available: true,
        structured_ingestion_available: true,
      } as ReadinessReport}
    />,
  )
  const user = userEvent.setup()
  expect(screen.getByRole('switch', { name: 'Azure Document Intelligence' })).toBeChecked()
  expect(screen.getByRole('switch', { name: 'Neo4j Read' })).toBeChecked()
  expect(screen.getByRole('switch', { name: 'Local + Neo4j Hybrid' })).toBeChecked()
  expect(screen.getByRole('switch', { name: 'Local + Neo4j Hybrid' })).toBeDisabled()
  const file = new File(['structured content'], 'structured.pdf', { type: 'application/pdf' })
  await user.upload(screen.getByLabelText('PDF document'), file)
  await user.click(screen.getByRole('button', { name: 'Add to knowledge' }))
  const calls = fetch.mock.calls as unknown as [string, RequestInit][]
  const body = calls[0][1].body as FormData
  expect(body.get('document_processing')).toBe('azure_di')
  expect(body.get('ingestion_mode')).toBe('structured_graph')
  expect(await screen.findByText('Extracting → Ready')).toBeInTheDocument()
})

test('URL ingestion explains disabled configuration and reports searchable provenance', async () => {
  const disabled = render(
    <KnowledgePanel readiness={{ url_ingestion_enabled: false } as ReadinessReport} />,
  )
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'URL' }))
  expect(screen.getByText(/Set ALLOW_URL_INGEST=true/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Add to knowledge' })).toBeDisabled()
  disabled.unmount()

  const fetch = vi.fn(async (path: string, _init?: RequestInit) => Response.json(
    path === '/ingest/url'
      ? {
          pages: 1,
          chunks: 2,
          duplicate: false,
          sources: [{
            title: 'MFA Guide',
            url: 'https://example.com/mfa',
            chunks: 2,
            duplicate: false,
          }],
        }
      : { items: [] },
  ))
  vi.stubGlobal('fetch', fetch)
  render(<KnowledgePanel readiness={{ url_ingestion_enabled: true } as ReadinessReport} />)
  await user.click(screen.getByRole('button', { name: 'URL' }))
  await user.type(screen.getByLabelText('Public page URL'), 'https://example.com/mfa')
  await user.click(screen.getByRole('button', { name: 'Add to knowledge' }))
  expect(await screen.findByText(/MFA Guide is searchable.*2 chunks indexed/)).toBeInTheDocument()
  const request = fetch.mock.calls.find(([path]) => path === '/ingest/url')
  expect(JSON.parse(request?.[1]?.body as string)).toEqual({
    url: 'https://example.com/mfa',
  })
})
