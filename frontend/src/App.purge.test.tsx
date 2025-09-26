import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

const healthPayload = {
  status: 'ok',
  provider: 'ollama',
  provider_type: 'local',
  model_name: 'phi3:mini',
  provider_vendor: null,
  local_model_available: true,
  operator_message: 'Local provider active.',
  hosted_reachable: true,
  hosted_model_name: 'llama-3.1-8b-instant',
  active_provider: 'ollama',
  active_model: 'phi3:mini',
  graph_backend: 'inmemory',
  preferred_local_models: ['phi3:mini', 'tinyllama'],
  ollama_reachable: true,
  llamacpp_reachable: false,
  neo4j_reachable: false,
  vector_store_path: 'store/chroma',
  vector_store_path_exists: true,
}

const askPayload = {
  answer: 'Mock answer with context.',
  provider: 'ollama',
  question: 'How do I reset MFA?',
  citations: [
    {
      doc_id: 'doc-1',
      chunk_id: 'doc-1-0',
      score: 0.2,
      title: 'Reset MFA',
      snippet: 'Follow these steps to reset MFA.',
    },
  ],
  planner: { mode: 'VECTOR', reasons: ['no graph entities'] },
  latency_ms: 42,
  confidence: 0.7,
}

const ingestPayload = {
  chunks: 2,
  entities: 3,
  vector_count: 2,
  ms: 15,
}

const jsonResponse = (payload: unknown, status = 200): Response =>
  new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })

const resolveUrl = (input: RequestInfo | URL): string => {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  if (typeof Request !== 'undefined' && input instanceof Request) {
    return input.url
  }
  return String(input)
}

describe('Purge memory flow', () => {
  const user = userEvent.setup()
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async (input, init) => {
      const url = resolveUrl(input)

      if (url.endsWith('/health')) {
        return jsonResponse(healthPayload)
      }

      if (url.endsWith('/ask')) {
        return jsonResponse(askPayload)
      }

      if (url.endsWith('/ingest/paste')) {
        return jsonResponse(ingestPayload)
      }

      return jsonResponse({ detail: 'not found' }, 404)
    })

    globalThis.fetch = fetchMock as unknown as typeof fetch
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('requires DELETE confirmation before purging', async () => {
    render(<App />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: /purge memory/i }))

    const confirmationInput = await screen.findByLabelText(/Confirmation/i)
    await user.type(confirmationInput, 'nope')
    await user.click(screen.getByRole('button', { name: /confirm purge/i }))

    expect(screen.getByText(/Confirmation text does not match DELETE/i)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('clears conversation and ingest state after purge confirmation', async () => {
    render(<App />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())

    const messageInput = screen.getByLabelText('Message input')
    await user.type(messageInput, 'How do I reset MFA?')
    await user.click(screen.getByRole('button', { name: /^ask$/i }))

    await screen.findAllByText('Mock answer with context.')

    const pasteField = screen.getByLabelText(/Paste content/i)
    await user.type(pasteField, 'Step 1\nStep 2')
    await user.click(screen.getByRole('button', { name: /upload and index/i }))

    await screen.findByText(/Indexed/)

    await user.click(screen.getByRole('button', { name: /purge memory/i }))
    const confirmationInput = await screen.findByLabelText(/Confirmation/i)
    await user.type(confirmationInput, 'DELETE')
    await user.click(screen.getByRole('button', { name: /confirm purge/i }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    expect(screen.getByText('Ask a real service desk question to see cited answers.')).toBeInTheDocument()
    expect(screen.getByText(/Try “What is the MFA reset policy/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Paste content/i)).toHaveValue('')
    expect(screen.queryByText(/Indexed/)).not.toBeInTheDocument()
    expect(screen.queryByText('Mock answer with context.')).not.toBeInTheDocument()
  })
})
