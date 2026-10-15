import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import InvestigationActivity from './InvestigationActivity'
import { mergeActivity, type ActivityEvent } from './investigationStream'

const event: ActivityEvent = {
  sequence: 4,
  kind: 'research_query',
  stage: 'research',
  status: 'running',
  summary: 'Searching the web for the VPN client error',
  query: 'vendor VPN error 720 documentation',
  rationale: 'The exact error changes the recovery path.',
  objective: 'Determine whether the documented VPN recovery path applies.',
  research_questions: ['What does error 720 mean?', 'Which client versions are supported?'],
  findings: ['Error 720 reference', 'Supported client matrix'],
  missing_information: ['The installed client version'],
  next_action: 'Compare the installed version with the support matrix.',
  sources: [{ title: 'Vendor troubleshooting guide', url: 'https://example.com/guide' }],
}

test('renders actual research decisions and source provenance without hidden reasoning', () => {
  const cancel = vi.fn()
  render(<InvestigationActivity events={[event]} busy onCancel={cancel} />)
  expect(screen.getByText(event.query as string)).toBeInTheDocument()
  expect(screen.getByText(event.rationale as string)).toBeInTheDocument()
  expect(screen.getByText(event.objective as string)).toBeInTheDocument()
  expect(screen.getByText('What does error 720 mean?')).toBeInTheDocument()
  expect(screen.getByText('Error 720 reference')).toBeInTheDocument()
  expect(screen.getByText('The installed client version')).toBeInTheDocument()
  expect(screen.getByText(event.next_action as string)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Vendor troubleshooting guide' })).toHaveAttribute('href', 'https://example.com/guide')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel investigation' }))
  expect(cancel).toHaveBeenCalledOnce()
})

test('duplicate streamed events are ignored by stable sequence or event id', () => {
  expect(mergeActivity([event], event)).toEqual([event])
  expect(mergeActivity([event], { ...event, sequence: 5, event_id: 'research:query:1' })).toHaveLength(2)
  expect(mergeActivity(
    [event, { ...event, sequence: 5, event_id: 'research:query:1' }],
    { ...event, sequence: 6, event_id: 'research:query:1' },
  )).toHaveLength(2)
})
