import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
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

test('workspace navigation switches focused views without breaking knowledge inputs', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [] })))
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: /^Knowledge$/ }))
  expect(screen.getByRole('button', { name: /^Knowledge$/ })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByLabelText('Chat workspace')).not.toBeVisible()
  expect(screen.getByLabelText('Knowledge workspace')).toBeVisible()
  expect(screen.getByLabelText('Knowledge text').tagName).toBe('TEXTAREA')
  vi.restoreAllMocks()
})

test('general conversation sends follow-ups to the existing request', async () => {
  const current = { ...initial, workflow: 'knowledge', status: 'assessment_ready', final_answer: 'Toronto is in Ontario.', chat_messages: [{ role: 'user', content: 'Where is Toronto?' }, { role: 'assistant', content: 'Toronto is in Ontario.' }] }
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/cases') return { ok: true, json: async () => [] }
    if (url.endsWith('/message')) expect(JSON.parse(init?.body as string)).toEqual({ summary: 'Which country?' })
    return { ok: true, json: async () => ({ request_id: 'chat-one', case_id: null, result: current, events: [] }) }
  })
  vi.stubGlobal('fetch', fetch)
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByLabelText('Question or ticket'), 'Where is Toronto?')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  await user.type(await screen.findByLabelText('Reply to this conversation'), 'Which country?')
  await user.click(within(screen.getByLabelText('Conversation')).getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/requests/chat-one/message')).toBe(true))
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
            source: 'azure_openai',
            content: 'Ticket analyzed',
            created_at: '2026-10-08T10:00:00Z',
          },
        ],
      }
    if (url === '/ask' && init?.method === 'POST') {
      created = true
      current = {
        ...initial,
        status: 'needs_input',
        pending_question: 'Which site or floor is Jake Peralta currently working from?',
      }
      return {
        ok: true,
        json: async () => ({
          request_id: current.case_id,
          case_id: current.case_id,
          result: current,
          events: [
            {
              id: 1,
              tool: 'analyze_ticket',
              source: 'azure_openai',
              content: 'Ticket analyzed',
              created_at: '2026-10-08T10:00:00Z',
            },
          ],
        }),
      }
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
  await user.type(screen.getByLabelText('Question or ticket'), initial.ticket.summary)
  await user.click(screen.getByRole('button', { name: 'Send' }))
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
      json: async () => (init ? { detail: 'Azure OpenAI request failed' } : []),
    })),
  )
  render(<App />)
  await user.type(screen.getByLabelText('Question or ticket'), 'Incident details')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('DeskMate cannot complete this request right now')
  expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
})

test.each([
  [502, '', 'The DeskMate server is temporarily unavailable (HTTP 502). Please try again.'],
  [
    503,
    '<html>Service unavailable</html>',
    'The DeskMate server is temporarily unavailable (HTTP 503). Please try again.',
  ],
  [
    200,
    '',
    'DeskMate received an empty or invalid server response. Reload the page and try again.',
  ],
])('handles a non-JSON %i response without a parser error', async (status, body, message) => {
  const fetch = vi.fn(async (url: string) =>
    url === '/cases' ? Response.json([]) : new Response(body, { status }),
  )
  vi.stubGlobal('fetch', fetch)
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByLabelText('Question or ticket'), 'An incident')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  expect(fetch.mock.calls.filter(([url]) => url === '/ask')).toHaveLength(1)
})

test('can submit again after an empty proxy response', async () => {
  let calls = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/cases') return Response.json([])
      if (url === '/readiness') return Response.json({})
      if (url === '/documents') return Response.json([])
      if (url === '/speech/readiness') return Response.json({ ready: false })
      if (++calls === 1) return new Response('', { status: 502 })
      return Response.json({
        request_id: 'knowledge-request',
        case_id: null,
        result: {
          ...initial,
          workflow: 'knowledge',
          status: 'assessment_ready',
          final_answer: 'Abdul graduated in 2022.',
        },
        events: [],
      })
    }),
  )
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByLabelText('Question or ticket'), 'What year did Abdul graduate?')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByText('Abdul graduated in 2022.')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('shows a connection error when the request service is unreachable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/cases') return Response.json([])
      throw new TypeError('Failed to fetch')
    }),
  )
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByLabelText('Question or ticket'), 'An incident')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'DeskMate could not connect to the server. Please try again.',
  )
  expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
})

test('preserves the provider error and restores its saved case for retry', async () => {
  const current = { ...initial, status: 'error' }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/ask')
        return Response.json(
          { detail: 'Azure OpenAI request failed (HTTP 429, TPD quota limit)', case_id: current.case_id },
          { status: 502 },
        )
      if (url.endsWith('/events')) return Response.json([])
      return Response.json(url === '/cases' ? [current] : current)
    }),
  )
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByLabelText('Question or ticket'), 'An incident')
  await user.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'DeskMate cannot complete this request right now',
  )
  expect(await screen.findByRole('button', { name: 'Retry Investigation' })).toBeEnabled()
  expect(screen.getByTestId('case-id')).toHaveTextContent(current.case_id)
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
  await user.click(screen.getByRole('button', { name: /^Investigations$/ }))
  await user.click(await screen.findByRole('button', { name: /Jake Peralta from B99/ }))
  expect(await screen.findByText('Remembered facts from previous cases')).toBeInTheDocument()
  expect(screen.getByText('B99 2nd floor')).toBeInTheDocument()
  expect(screen.getByText(/Sample data/)).toBeInTheDocument()
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
  await user.click(screen.getByRole('button', { name: /^Knowledge$/ }))
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
  await user.click(screen.getByRole('button', { name: /^Investigations$/ }))
  await user.selectOptions(screen.getByLabelText('Status'), 'failed')
  await user.click(await screen.findByRole('button', { name: /Jake Peralta from B99/ }))
  await user.click(await screen.findByRole('button', { name: 'Retry Investigation' }))
  expect(await screen.findByText('Which site?')).toBeInTheDocument()
  expect(screen.getByTestId('case-id')).toHaveTextContent('case-unit')
})

test('knowledge answer, contradictions, and activity render without a ticket question', async () => {
  const user = userEvent.setup()
  const current: Case = {
    ...initial,
    workflow: 'knowledge',
    status: 'assessment_ready',
    ticket: { summary: 'What year did Abdul graduate?' },
    final_answer: 'Abdul graduated in 2022.',
    final_response: { answer: 'Abdul graduated in 2022.' },
    contradictions: ['Two documents give different dates.'],
    verification: { justification: 'Private internal draft' },
    hypotheses: [
      {
        claim: 'The newer resume supersedes the old version.',
        confidence: 0.6,
        status: 'plausible',
      },
    ],
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      json: async () =>
        url === '/cases'
          ? [current]
          : url.endsWith('/events')
            ? [
                {
                  id: 1,
                  tool: 'search_knowledge',
                  source: 'chroma',
                  content: 'Private internal draft',
                  created_at: '2026-10-08T10:00:00Z',
                },
              ]
            : current,
    })),
  )
  render(<App />)
  await user.click(screen.getByRole('button', { name: /^Investigations$/ }))
  await user.click(await screen.findByRole('button', { name: /What year did Abdul graduate/ }))
  expect(await screen.findByRole('heading', { name: 'Final Answer' })).toBeInTheDocument()
  expect(screen.getByText('Abdul graduated in 2022.').tagName).toBe('STRONG')
  expect(screen.getByText('Two documents give different dates.')).toBeInTheDocument()
  expect(screen.getByText('The newer resume supersedes the old version.')).toBeInTheDocument()
  expect(screen.queryByLabelText('Your answer')).not.toBeInTheDocument()
  await user.click(screen.getByText('Investigation progress'))
  expect(screen.getAllByTestId('investigation-scroll')[0]).toBeVisible()
  expect(screen.queryByText('Private internal draft')).not.toBeInTheDocument()
})
