import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import App, { type Case } from './App'

const initial: Case = {
  case_id: 'case-unit',
  thread_id: 'case-unit',
  ticket: { summary: 'Jake Peralta from B99 cannot connect to VPN.' },
  status: 'new',
  pending_question: null,
  likely_issue: null,
  hypotheses: [],
  confirmed_facts: [],
  remembered_facts: [],
  inferences: [],
  recommended_next_action: null,
  confidence: 0,
  uncertainty: [],
  evidence: [],
  final_answer: null,
}
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('creates a case, shows real event data, resumes human input and renders assessment', async () => {
  const user = userEvent.setup()
  let current = initial
  let created = false
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/events'))
      return {
        ok: true,
        json: async () => [
          {
            id: 1,
            tool: 'analyze_ticket',
            source: 'groq',
            content: 'Ticket analyzed',
            created_at: '2026-10-08T10:00:00Z',
          },
        ],
      }
    if (url === '/cases' && init?.method === 'POST') {
      created = true
      return { ok: true, json: async () => current }
    }
    if (url.endsWith('/investigate'))
      current = {
        ...initial,
        status: 'needs_input',
        pending_question: 'Which site or floor is Jake Peralta currently working from?',
      }
    if (url.endsWith('/resume')) {
      expect(JSON.parse(init?.body as string)).toEqual({ answer: '2nd floor' })
      current = {
        ...initial,
        status: 'assessment_ready',
        likely_issue: 'Configuration change',
        recommended_next_action: 'Refresh the client profile',
        confidence: 0.7,
        uncertainty: ['Resolution unverified'],
      }
    }
    return { ok: true, json: async () => (url === '/cases' ? (created ? [current] : []) : current) }
  })
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  await user.type(screen.getByLabelText('Ticket details'), initial.ticket.summary)
  await user.click(screen.getByRole('button', { name: 'Start Investigation' }))
  expect(
    await screen.findByText('Which site or floor is Jake Peralta currently working from?'),
  ).toBeInTheDocument()
  expect(screen.getByText('Ticket analyzed')).toBeInTheDocument()
  expect(screen.getByTestId('case-id')).toHaveTextContent('case-unit')
  await user.type(screen.getByLabelText('Your answer'), '2nd floor')
  await user.click(screen.getByRole('button', { name: 'Continue Investigation' }))
  expect(await screen.findByText('Refresh the client profile')).toBeInTheDocument()
  expect(screen.getByText(/70%/)).toBeInTheDocument()
  expect(screen.getByText('Resolution unverified')).toBeInTheDocument()
  expect(screen.queryByLabelText('Your answer')).not.toBeInTheDocument()
  expect(fetch.mock.calls.some(([url]) => url === '/cases/case-unit/resume')).toBe(true)
})

test('renders explicit API errors and restores the submit control', async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url, init) => ({
      ok: !init,
      json: async () => (init ? { detail: 'Groq request failed' } : []),
    })),
  )
  render(<App />)
  await user.type(screen.getByLabelText('Ticket details'), 'Incident details')
  await user.click(screen.getByRole('button', { name: 'Start Investigation' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Groq request failed')
  expect(screen.getByRole('button', { name: 'Start Investigation' })).toBeEnabled()
})

test('shows remembered facts and fixture provenance when reopening a case', async () => {
  const c = {
    ...initial,
    status: 'assessment_ready',
    remembered_facts: [
      {
        subject: 'Jake Peralta',
        predicate: 'current_site',
        value: 'B99 2nd floor',
        source: 'human_confirmed',
        source_system: 'human_input',
        observed_at: '2026-10-08T10:00:00Z',
        case_id: 'previous-case',
      },
    ],
    evidence: [
      {
        tool: 'get_active_alerts',
        source: 'B99 example dataset',
        source_system: 'local_fixture',
        observed_at: '2026-10-08T10:00:00Z',
        content: { message: 'Active VPN authentication alert' },
      },
    ],
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (url === '/cases' ? [c] : url.endsWith('/events') ? [] : c),
    })),
  )
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: /Jake Peralta from B99/ }))
  expect(await screen.findByText('Remembered facts from previous cases')).toBeInTheDocument()
  expect(screen.getByText('B99 2nd floor')).toBeInTheDocument()
  expect(screen.getByText('Fixture · local_fixture')).toBeInTheDocument()
})

test('ingests pasted knowledge and shows duplicate feedback', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (url === '/cases' ? [] : { chunks: 0, duplicate: true }),
    })),
  )
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByLabelText('Knowledge text'), 'VPN recovery guide')
  await user.click(screen.getByRole('button', { name: 'Add to knowledge' }))
  await waitFor(() =>
    expect(screen.getByText('This content is already in knowledge.')).toBeInTheDocument(),
  )
})

test('retries the saved case after a service failure', async () => {
  let current: Case = { ...initial, status: 'error' }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/events')) return { ok: true, json: async () => [] }
      if (url.endsWith('/investigate') && init)
        current = { ...initial, status: 'needs_input', pending_question: 'Which site?' }
      return { ok: true, json: async () => (url === '/cases' ? [current] : current) }
    }),
  )
  const user = userEvent.setup()
  render(<App />)
  await user.click(await screen.findByRole('button', { name: /Jake Peralta from B99/ }))
  await user.click(await screen.findByRole('button', { name: 'Retry Investigation' }))
  expect(await screen.findByText('Which site?')).toBeInTheDocument()
  expect(screen.getByTestId('case-id')).toHaveTextContent('case-unit')
})
