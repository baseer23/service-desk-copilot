import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Composer from './components/Composer'
import MessageBubble, { Citation, Message } from './components/MessageBubble'

const API_BASE =
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.DEV ? 'http://localhost:8000' : typeof window !== 'undefined' ? window.location.origin : '')
const ADMIN_SECRET = import.meta.env.VITE_ADMIN_API_SECRET ?? ''

type ProviderOption = 'ollama' | 'groq'
const PROVIDER_STORAGE_KEY = 'deskMate.providerPreference'

type AskResponse = {
  answer: string
  provider: string
  question: string
  citations: Citation[]
  planner: Record<string, unknown>
  latency_ms: number
  confidence: number
}

type IngestResult = {
  chunks: number
  entities: number
  vector_count: number
  ms: number
  pages?: number
}

type HealthResponse = {
  status: string
  provider: string
  provider_type: string
  model_name: string
  provider_vendor: string | null
  local_model_available: boolean
  operator_message: string | null
  hosted_reachable: boolean
  hosted_model_name: string | null
  active_provider: string
  active_model: string
  graph_backend: string
  preferred_local_models: string[]
  ollama_reachable: boolean
  llamacpp_reachable: boolean
  neo4j_reachable: boolean
  vector_store_path: string
  vector_store_path_exists: boolean
}

type ProviderAdminResponse = {
  active_provider: string
  model_name: string
  provider: string
  provider_type: string
  reason: string | null
}

const INITIAL_PROMPT = 'Ask a real service desk question to see cited answers.'

type IndexedSource = {
  title: string
  tokens: number
  chunks: number
  mode: 'paste' | 'pdf'
  content?: string
  file?: File
}

async function postJSON<T>(
  url: string,
  body: unknown,
  options: { headers?: Record<string, string> } = {},
): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(options.headers ?? {}) },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText || 'Unexpected error' }))
    throw new Error(detail.detail ?? response.statusText)
  }
  return response.json() as Promise<T>
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [recentQuestions, setRecentQuestions] = useState<string[]>([])
  const [ingestMode, setIngestMode] = useState<'paste' | 'pdf'>('paste')
  const [pasteTitle, setPasteTitle] = useState('')
  const [pasteText, setPasteText] = useState('')
  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [indexStatus, setIndexStatus] = useState<IngestResult | null>(null)
  const [indexError, setIndexError] = useState<string | null>(null)
  const [indexedSource, setIndexedSource] = useState<IndexedSource | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [providerChoice, setProviderChoice] = useState<ProviderOption>('ollama')
  const [providerSaving, setProviderSaving] = useState(false)
  const [providerError, setProviderError] = useState<string | null>(null)
  const [attemptedRestore, setAttemptedRestore] = useState(false)
  const threadRef = useRef<HTMLDivElement | null>(null)
  const healthFailureMessage = `Backend ${API_BASE} not reachable — run make dev and ensure VITE_API_BASE points to the backend`

  const fetchHealthData = useCallback(async (): Promise<HealthResponse> => {
    const response = await fetch(`${API_BASE}/health`)
    if (!response.ok) throw new Error(response.statusText || 'Health check failed')
    return (await response.json()) as HealthResponse
  }, [API_BASE])

  const refreshHealth = useCallback(async () => {
    try {
      const data = await fetchHealthData()
      setHealth(data)
      setHealthError(null)
      return data
    } catch (error) {
      setHealth(null)
      setHealthError(healthFailureMessage)
      throw error
    }
  }, [fetchHealthData, healthFailureMessage])

  const lastAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant' && !messages[i].pending) {
        return messages[i]
      }
    }
    return null
  }, [messages])

  const lastAnswerSummary = useMemo(() => {
    if (!lastAssistant) return null
    const flattened = lastAssistant.text.replace(/\s+/g, ' ').trim()
    if (!flattened) return null
    const sentenceMatch = flattened.match(/.*?[.!?](?:\s|$)/)
    const candidate = sentenceMatch ? sentenceMatch[0].trim() : flattened
    const trimmed = candidate.length > 110 ? `${candidate.slice(0, 110).trimEnd()}…` : candidate
    return trimmed
  }, [lastAssistant])

  const lastAnswerSourceTitle = useMemo(() => {
    if (!lastAssistant) return 'Untitled'
    if (!lastAssistant.citations || lastAssistant.citations.length === 0) return 'Untitled'
    const firstTitled = lastAssistant.citations.find((citation) => citation.title && citation.title.trim().length > 0)
    const title = firstTitled?.title?.trim()
    return title && title.length > 0 ? title : 'Untitled'
  }, [lastAssistant])

  const providerPill = useMemo(() => {
    if (!health) return 'Provider · unknown · unknown'
    const rawKey = (health.active_provider || health.provider || 'unknown').toLowerCase()
    const typeLabel = rawKey === 'groq' ? 'api' : rawKey
    const preferredModel = (health.active_model || '').trim() || (health.model_name || '').trim()
    const fallbackModel = preferredModel || health.provider
    const model = fallbackModel && fallbackModel.length > 0 ? fallbackModel : 'unknown'
    return `Provider · ${typeLabel} · ${model}`
  }, [health])

  const graphPill = useMemo(() => {
    if (!health) return 'Graph · fallback · offline'
    const backend = (health.graph_backend || 'inmemory').toLowerCase()
    const backendLabel = backend === 'aura' ? 'Aura' : 'Fallback'
    const statusLabel = backend === 'aura' ? (health.neo4j_reachable ? 'online' : 'offline') : 'vector-only'
    return `Graph · ${backendLabel} · ${statusLabel}`
  }, [health])

  const graphPillClass = useMemo(() => {
    if (!health) return 'graph-pill warn'
    const online = health.graph_backend === 'aura' && health.neo4j_reachable
    return `graph-pill${online ? ' online' : ' warn'}`
  }, [health])

  const providerNotes = useMemo(() => {
    if (!health) return [] as Array<{ text: string; tone: 'default' | 'warn' }>
    const notes: Array<{ text: string; tone: 'default' | 'warn' }> = []
    if (health.operator_message) {
      notes.push({ text: health.operator_message, tone: health.local_model_available ? 'default' : 'warn' })
    }
    if (!health.local_model_available && (health.preferred_local_models?.length ?? 0) > 0) {
      notes.push({
        text: `Preferred small models: ${health.preferred_local_models.join(' → ')}.`,
        tone: 'default',
      })
    }
    if (!health.ollama_reachable) {
      notes.push({ text: 'Ollama endpoint unreachable – local provider will fall back to stub.', tone: 'warn' })
    }
    if (!health.hosted_reachable) {
      notes.push({ text: 'Groq endpoint unreachable – hosted calls fall back to stub.', tone: 'warn' })
    } else if (health.active_provider === 'groq') {
      notes.push({ text: 'Groq endpoint reachable.', tone: 'default' })
    }
    return notes
  }, [health])

  const providerMeta = useMemo(() => {
    if (!health) return null
    const rawModel = (health.active_model || health.model_name || '').trim()
    const activeModel = rawModel.length > 0 ? rawModel : 'unknown'
    const ollamaStatus = health.ollama_reachable ? 'Ollama: online' : 'Ollama: offline'
    const groqStatus = health.hosted_reachable ? 'Groq: online' : 'Groq: offline'
    return `Active: ${activeModel} · ${ollamaStatus} · ${groqStatus}`
  }, [health])

  const graphFallbackMessage = useMemo(() => {
    if (!health) return null
    if (health.graph_backend !== 'aura') {
      return 'Graph database unavailable — operating in vector-only mode.'
    }
    if (!health.neo4j_reachable) {
      return 'Neo4j Aura connection down — results will use vector fallback only.'
    }
    return null
  }, [health])

  const applyProvider = useCallback(
    async (target: ProviderOption, options: { restore?: boolean } = {}) => {
      if (!health) return
      if (health.active_provider === target) {
        setProviderChoice(target)
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(PROVIDER_STORAGE_KEY, target)
        }
        return
      }
      if (providerSaving) return
      setProviderChoice(target)
      setProviderSaving(true)
      setProviderError(null)
      const headers: Record<string, string> = ADMIN_SECRET ? { 'x-admin-secret': ADMIN_SECRET } : {}
      try {
        await postJSON<ProviderAdminResponse>(`${API_BASE}/admin/provider`, { provider: target }, { headers })
        if (typeof window !== 'undefined') {
          window.localStorage.setItem(PROVIDER_STORAGE_KEY, target)
        }
        await refreshHealth()
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Failed to switch provider'
        setProviderError(message)
        if (typeof window !== 'undefined' && options.restore) {
          window.localStorage.removeItem(PROVIDER_STORAGE_KEY)
        }
        await refreshHealth().catch(() => undefined)
      } finally {
        setProviderSaving(false)
      }
    },
    [ADMIN_SECRET, health, providerSaving, refreshHealth],
  )

  useEffect(() => {
    if (!health) return
    const activeKey: ProviderOption = health.active_provider === 'groq' ? 'groq' : 'ollama'
    setProviderChoice(activeKey)
    if (!attemptedRestore) {
      const saved =
        typeof window !== 'undefined'
          ? (window.localStorage.getItem(PROVIDER_STORAGE_KEY) as ProviderOption | null)
          : null
      if (saved && saved !== activeKey) {
        void applyProvider(saved, { restore: true })
      }
      setAttemptedRestore(true)
    }
  }, [health, attemptedRestore, applyProvider])

  const scrollToBottom = () => {
    requestAnimationFrame(() => {
      const node = threadRef.current
      if (node) node.scrollTop = node.scrollHeight
    })
  }

  const handleAsk = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed) return

    const timestamp = Date.now()
    const userMessage: Message = { id: `u-${timestamp}`, role: 'user', text: trimmed }
    const assistantPlaceholder: Message = {
      id: `a-${timestamp}`,
      role: 'assistant',
      text: 'Thinking…',
      pending: true,
      citations: [],
    }

    setMessages((prev) => [...prev, userMessage, assistantPlaceholder])
    setRecentQuestions((prev) => [trimmed, ...prev.filter((q) => q !== trimmed)].slice(0, 5))
    setLoading(true)
    scrollToBottom()

    try {
      const response = await postJSON<AskResponse>(`${API_BASE}/ask`, {
        question: trimmed,
        provider_override: providerChoice,
      })
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantPlaceholder.id
            ? {
                ...message,
                text: response.answer,
                pending: false,
                citations: response.citations,
                metadata: { planner: response.planner, latency: response.latency_ms, confidence: response.confidence },
              }
            : message,
        ),
      )
    } catch (error) {
      const fallback = error instanceof Error ? error.message : 'Request failed'
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantPlaceholder.id
            ? {
                ...message,
                text: `I could not reach the backend (${fallback}). Please ensure it is running on port 8000.`,
                pending: false,
                error: true,
              }
            : message,
        ),
      )
    } finally {
      setLoading(false)
      scrollToBottom()
    }
  }

  const handleRecentQuestionClick = (question: string) => {
    void handleAsk(question)
  }

  const resetThread = () => {
    setMessages([])
  }

  const handlePasteIngest = async () => {
    setIndexError(null)
    const trimmedText = pasteText.trim()
    if (!trimmedText) {
      setIndexError('Provide some text to index.')
      return
    }
    setIndexStatus(null)
    try {
      const title = pasteTitle.trim() || 'Untitled'
      const result = await postJSON<IngestResult>(`${API_BASE}/ingest/paste`, {
        title,
        text: trimmedText,
      })
      setIndexStatus(result)
      setIndexedSource({
        title,
        tokens: result.vector_count,
        chunks: result.chunks,
        mode: 'paste',
        content: trimmedText,
      })
      setPasteTitle('')
      setPasteText('')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to index text'
      setIndexError(message)
    }
  }

  const handlePdfIngest = async () => {
    setIndexError(null)
    if (!pdfFile) {
      setIndexError('Select a PDF file to upload.')
      return
    }
    setIndexStatus(null)
    const formData = new FormData()
    formData.append('file', pdfFile)
    try {
      const title = pdfFile.name || 'Untitled'
      const response = await fetch(`${API_BASE}/ingest/pdf`, {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => ({ detail: response.statusText || 'Unexpected error' }))
        throw new Error(detail.detail ?? response.statusText)
      }
      const result = (await response.json()) as IngestResult
      setIndexStatus(result)
      setIndexedSource({
        title,
        tokens: result.vector_count,
        chunks: result.chunks,
        mode: 'pdf',
        file: pdfFile,
      })
      setPdfFile(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to index PDF'
      setIndexError(message)
    }
  }

  const handleIngest = () => {
    if (ingestMode === 'paste') {
      handlePasteIngest()
    } else {
      handlePdfIngest()
    }
  }

  const clearIngestForm = () => {
    setPasteTitle('')
    setPasteText('')
    setPdfFile(null)
    setIndexError(null)
    setIndexStatus(null)
    setIndexedSource(null)
  }

  const viewIndexedSource = () => {
    if (!indexedSource) return
    if (typeof window === 'undefined') return
    if (indexedSource.mode === 'paste' && indexedSource.content) {
      const blob = new Blob([indexedSource.content], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    } else if (indexedSource.mode === 'pdf' && indexedSource.file) {
      const url = URL.createObjectURL(indexedSource.file)
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 2000)
    }
  }

  useEffect(() => {
    let cancelled = false
    const loadHealth = async () => {
      try {
        const data = await fetchHealthData()
        if (!cancelled) {
          setHealth(data)
          setHealthError(null)
        }
      } catch (error) {
        if (!cancelled) {
          setHealth(null)
          setHealthError(healthFailureMessage)
        }
      }
    }

    void loadHealth()
    return () => {
      cancelled = true
    }
  }, [fetchHealthData, healthFailureMessage])

  return (
    <div className="app-page">
      <div className="app-container">
        {healthError && <div className="health-banner">{healthError}</div>}
        <header className="chat-header">
          <div className="title-stack">
            <h1>DeskMate</h1>
            <p className="subtitle">A service desk copilot</p>
            <p className="tagline">Ingest docs. Ask with citations.</p>
          </div>
          <div className="header-actions">
            <div className="provider-stack">
              <div className="provider-row">
                <span className="provider-pill">{providerPill}</span>
                <span className={graphPillClass}>{graphPill}</span>
                <div className={`provider-toggle${providerSaving ? ' busy' : ''}`} role="group" aria-label="Select provider">
                  {(['ollama', 'groq'] as ProviderOption[]).map((option) => {
                    const isSelected = providerChoice === option
                    const isServerActive = health?.active_provider === option
                    return (
                      <button
                        key={option}
                        type="button"
                        className={`provider-toggle-option${isSelected ? ' active' : ''}`}
                        disabled={!health || providerSaving || isServerActive}
                        onClick={() => applyProvider(option)}
                        aria-pressed={isSelected}
                        title={option === 'ollama' ? 'Use Ollama for responses' : 'Use Groq for responses'}
                      >
                        {option === 'ollama' ? 'Ollama' : 'Groq'}
                      </button>
                    )
                  })}
                </div>
              </div>
              {providerMeta && <span className="provider-meta">{providerMeta}</span>}
              {providerError && <span className="provider-note warn">{providerError}</span>}
              {providerNotes.map((note, index) => (
                <span key={index} className={`provider-note${note.tone === 'warn' ? ' warn' : ''}`}>
                  {note.text}
                </span>
              ))}
            </div>
            <button type="button" className="ghost" onClick={resetThread} disabled={loading}>
              New thread
            </button>
          </div>
        </header>

        {graphFallbackMessage && (
          <div className="inline-banner warn" role="status">
            <span>{graphFallbackMessage}</span>
          </div>
        )}

        <section className="ingest-panel">
          <div className="panel-header">
            <h2>Knowledge ingestion</h2>
            <div className="ingest-tabs" role="tablist" aria-label="Select indexing mode">
              <button
                type="button"
                role="tab"
                aria-selected={ingestMode === 'paste'}
                className={ingestMode === 'paste' ? 'active' : ''}
                onClick={() => setIngestMode('paste')}
              >
                Paste
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={ingestMode === 'pdf'}
                className={ingestMode === 'pdf' ? 'active' : ''}
                onClick={() => setIngestMode('pdf')}
              >
                Upload PDF
              </button>
            </div>
          </div>

          {indexStatus && indexedSource && (
            <div className="inline-banner success" role="status">
              <p className="banner-text">
                <span className="banner-segment">Indexed “{indexedSource.title}”</span>
                <span className="banner-separator" aria-hidden="true">
                  •
                </span>
                <span className="banner-segment">{indexStatus.vector_count} vectors stored locally</span>
                <span className="banner-separator" aria-hidden="true">
                  •
                </span>
                <span className="banner-segment">{indexStatus.chunks} chunks</span>
              </p>
              <button type="button" className="link-button banner-link" onClick={viewIndexedSource}>
                View source
              </button>
            </div>
          )}

          {indexError && (
            <div className="inline-banner error" role="alert">
              <span>Indexing failed. Try again.</span>
              <button type="button" className="link-button" onClick={handleIngest}>
                Retry
              </button>
            </div>
          )}

          {ingestMode === 'paste' ? (
            <div className="ingest-form">
              <label className="input-label" htmlFor="source-title">
                Name this source optional
              </label>
              <input
                id="source-title"
                type="text"
                value={pasteTitle}
                onChange={(event) => setPasteTitle(event.target.value)}
              />
              <label className="input-label" htmlFor="source-text">
                Paste content
              </label>
              <textarea
                id="source-text"
                placeholder="Paste or drop your playbook"
                value={pasteText}
                onChange={(event) => setPasteText(event.target.value)}
              />
            </div>
          ) : (
            <div className="ingest-form">
              <label className="input-label" htmlFor="source-file">
                Upload a PDF
              </label>
              <input
                id="source-file"
                type="file"
                accept="application/pdf"
                onChange={(event) => setPdfFile(event.target.files?.[0] ?? null)}
              />
              {pdfFile && <span className="file-chip">{pdfFile.name}</span>}
            </div>
          )}

          <div className="ingest-actions">
            <button type="button" className="primary" onClick={handleIngest} disabled={loading}>
              Upload and index
            </button>
            <button type="button" className="quiet" onClick={clearIngestForm} disabled={loading}>
              Clear
            </button>
          </div>
        </section>

        <div className="main-content">
          <div className="chat-panel">
            <div className="chat-thread" ref={threadRef}>
              {messages.length === 0 ? (
                <div className="placeholder">{INITIAL_PROMPT}</div>
              ) : (
                messages.map((message) => <MessageBubble key={message.id} message={message} />)
              )}
            </div>
            <Composer onSend={handleAsk} disabled={loading} />
          </div>

          <aside className="sidebar">
            <h2>Recent questions</h2>
            {recentQuestions.length === 0 ? (
              <p className="muted">Try "What is the MFA reset policy" or "How do I create a ticket".</p>
            ) : (
              <ul className="recent-question-list">
                {recentQuestions.map((question) => (
                  <li key={question}>
                    <button
                      type="button"
                      className="recent-question"
                      onClick={() => handleRecentQuestionClick(question)}
                    >
                      {question}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {lastAssistant && (
              <div className="sidebar-card last-answer-card">
                <h3>Last answer</h3>
                <p className="last-answer-summary">{lastAnswerSummary ?? 'Answer ready.'}</p>
                <p className="last-answer-source">From: {lastAnswerSourceTitle}</p>
              </div>
            )}
            <button
              type="button"
              className="link-button sidebar-link"
              onClick={() => setRecentQuestions([])}
              disabled={recentQuestions.length === 0}
            >
              Clear history
            </button>
          </aside>
        </div>
      </div>
    </div>
  )
}
