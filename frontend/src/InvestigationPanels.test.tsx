import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import { EvidenceList, InvestigationTimeline } from './InvestigationPanels'

test('1000 timeline events stay inside one keyboard-accessible scroll region and hide model drafts', () => {
  const events = Array.from({ length: 1000 }, (_, id) => ({
    id,
    tool: 'reason',
    source: 'stub',
    content: 'Private model draft',
    created_at: '2026-10-12T00:00:00Z',
  }))
  render(<InvestigationTimeline events={events} />)
  expect(screen.getByRole('region', { name: 'Investigation timeline' })).toHaveClass(
    'investigation-scroll',
  )
  expect(screen.getByRole('region', { name: 'Investigation timeline' })).toHaveAttribute(
    'tabindex',
    '0',
  )
  expect(screen.getAllByRole('listitem')).toHaveLength(1000)
  expect(screen.queryByText('Private model draft')).not.toBeInTheDocument()
  expect(screen.queryByText(/stub/)).not.toBeInTheDocument()
})

test('large evidence is previewed; full content remains available only after expansion', async () => {
  const content = 'Evidence '.repeat(10000) + 'UNIQUE_FULL_END'
  render(
    <EvidenceList
      evidence={[
        {
          tool: 'fetch_web_page',
          source: 'Vendor guide',
          source_system: 'public_web',
          source_quality: 'third_party',
          url: 'https://third-party.example/guide',
          observed_at: '2026-10-12T00:00:00Z',
          content,
        },
      ]}
    />,
  )
  expect(screen.getByRole('region', { name: 'Sources and evidence' })).toHaveClass('evidence-list')
  expect(screen.getByText(/Third-party source/)).toBeInTheDocument()
  expect(screen.getByRole('link')).toHaveAttribute('href', 'https://third-party.example/guide')
  expect(screen.queryByText(/UNIQUE_FULL_END/)).not.toBeInTheDocument()
  await userEvent.click(screen.getByText('Show full evidence'))
  expect(await screen.findByLabelText('Full evidence')).toHaveTextContent('UNIQUE_FULL_END')
})

test('document evidence links to the authenticated original without exposing storage credentials', () => {
  const id = 'a'.repeat(64)
  render(<EvidenceList evidence={[{ tool: 'search_knowledge', source: 'Fictional handbook', source_system: 'azure_search', observed_at: '2026-10-13T00:00:00Z', content: { text: 'Fictional VPN guidance', metadata: { doc_id: id, title: 'VPN handbook', page: 2, extraction_method: 'document_intelligence', table_count: 1, embedding_model: 'private-model', tenant_id: 'private-tenant' } } }]} />)
  expect(screen.getByRole('link', { name: 'Download original document' })).toHaveAttribute('href', `/documents/${id}/download`)
  expect(screen.getByText(/Page 2/)).toBeInTheDocument()
  expect(screen.getByText(/Azure Document Intelligence layout · 1 table/)).toBeInTheDocument()
  expect(screen.queryByText(/azure_search|private-model|private-tenant/)).not.toBeInTheDocument()
})
