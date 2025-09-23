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

const INITIAL_PROMPT = "Ask a question about your service desk docs to see answers with citations."

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
  const [ingestStatus, setIngestStatus] = useState<IngestResult | null>(null)
  const [ingestError, setIngestError] = useState<string | null>(null)
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
    setIngestError(null)
    if (!pasteText.trim()) {
      setIngestError('Provide some text to ingest.')
      return
    }
    setIngestStatus(null)
    try {
      const result = await postJSON<IngestResult>(`${API_BASE}/ingest/paste`, {
        title: pasteTitle || 'Untitled Paste',
        text: pasteText,
      })
      setIngestStatus(result)
      setPasteText('')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to ingest text'
      setIngestError(message)
    }
  }

  const handlePdfIngest = async () => {
    setIngestError(null)
    if (!pdfFile) {
      setIngestError('Select a PDF file to upload.')
      return
    }
    setIngestStatus(null)
    const formData = new FormData()
    formData.append('file', pdfFile)
    try {
      const response = await fetch(`${API_BASE}/ingest/pdf`, {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => ({ detail: response.statusText || 'Unexpected error' }))
        throw new Error(detail.detail ?? response.statusText)
      }
      const result = (await response.json()) as IngestResult
      setIngestStatus(result)
      setPdfFile(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to ingest PDF'
      setIngestError(message)
    }
  }

  const handleIngest = () => {
    if (ingestMode === 'paste') {
      handlePasteIngest()
    } else {
      handlePdfIngest()
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
          <div>
            <h1>Service Desk Copilot</h1>
            <p>Hybrid GraphRAG playground. Ingest knowledge locally and ask questions with citations.</p>
          </div>
          <div className="header-actions">
            <span className="provider-pill">Provider: {health?.provider ?? 'loading…'}</span>
            <button type="button" className="ghost" onClick={resetThread} disabled={loading}>
              New thread
            </button>
          </div>
        </header>

        <section className="ingest-panel">
          <div className="ingest-tabs">
            <button
              type="button"
              className={ingestMode === 'paste' ? 'active' : ''}
              onClick={() => setIngestMode('paste')}
            >
              Paste
            </button>
            <button
              type="button"
              className={ingestMode === 'pdf' ? 'active' : ''}
              onClick={() => setIngestMode('pdf')}
            >
              PDF
            </button>
          </div>

          {ingestMode === 'paste' ? (
            <div className="ingest-form">
              <input
                type="text"
                placeholder="Title (optional)"
                value={pasteTitle}
                onChange={(event) => setPasteTitle(event.target.value)}
              />
              <textarea
                placeholder="Paste knowledge base text here..."
                value={pasteText}
                onChange={(event) => setPasteText(event.target.value)}
              />
            </div>
          ) : (
            <div className="ingest-form">
              <input
                type="file"
                accept="application/pdf"
                onChange={(event) => setPdfFile(event.target.files?.[0] ?? null)}
              />
              {pdfFile && <span className="file-chip">{pdfFile.name}</span>}
            </div>
          )}

          <div className="ingest-actions">
            <button type="button" onClick={handleIngest} disabled={loading}>
              Ingest
            </button>
            {ingestStatus && (
              <span className="ingest-result">
                Chunks: {ingestStatus.chunks} • Entities: {ingestStatus.entities} • Vectors: {ingestStatus.vector_count}
                {typeof ingestStatus.pages === 'number' ? ` • Pages: ${ingestStatus.pages}` : ''}
              </span>
            )}
            {ingestError && <span className="ingest-error">{ingestError}</span>}
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
              <p className="muted">Ask something to see it appear here.</p>
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
          </aside>
        </div>
      </div>
    </div>
  )
}
