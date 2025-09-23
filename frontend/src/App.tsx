import React, { useEffect, useMemo, useRef, useState } from 'react'
import Composer from './components/Composer'
import MessageBubble, { Citation, Message } from './components/MessageBubble'

const API_BASE =
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.DEV ? 'http://localhost:8000' : typeof window !== 'undefined' ? window.location.origin : '')

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
  ollama_reachable: boolean
  llamacpp_reachable: boolean
  neo4j_reachable: boolean
  vector_store_path: string
  vector_store_path_exists: boolean
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

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
  const threadRef = useRef<HTMLDivElement | null>(null)

  const lastAssistant = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'assistant' && !messages[i].pending) {
        return messages[i]
      }
    }
    return null
  }, [messages])

  const providerLabel = useMemo(() => {
    const provider = health?.provider
    if (!provider) return 'Ollama'
    if (provider.length === 0) return 'Ollama'
    return provider[0].toUpperCase() + provider.slice(1)
  }, [health?.provider])

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
      const response = await postJSON<AskResponse>(`${API_BASE}/ask`, { question: trimmed })
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
        const response = await fetch(`${API_BASE}/health`)
        if (!response.ok) throw new Error(response.statusText || 'Health check failed')
        const data = (await response.json()) as HealthResponse
        if (!cancelled) {
          setHealth(data)
          setHealthError(null)
        }
      } catch (error) {
        if (!cancelled) {
          const message = `Backend ${API_BASE} not reachable — run make dev and ensure VITE_API_BASE points to the backend`
          setHealth(null)
          setHealthError(message)
        }
      }
    }

    loadHealth()
    return () => {
      cancelled = true
    }
  }, [])

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
            <span className="provider-pill">Provider: {providerLabel}</span>
            <button type="button" className="ghost" onClick={resetThread} disabled={loading}>
              New thread
            </button>
          </div>
        </header>

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
              <span>
                Indexed "{indexedSource.title}" • {indexedSource.tokens} tokens • {indexedSource.chunks} chunks
              </span>
              <button type="button" className="link-button" onClick={viewIndexedSource}>
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
              <ul>
                {recentQuestions.map((question) => (
                  <li key={question}>{question}</li>
                ))}
              </ul>
            )}
            {lastAssistant && (
              <div className="sidebar-card">
                <h3>Last answer</h3>
                <p>{lastAssistant.text}</p>
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
