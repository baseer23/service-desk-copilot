import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { post, request } from './api'
import { type ReadinessReport } from './readiness'

interface Document {
  id: string
  title: string
  source_type: string
  pages: number
}
interface Match {
  id: string
  text: string
  storage: string
  metadata: { title?: string; doc_id?: string }
}

export default function KnowledgePanel({
  readiness,
}: {
  readiness: ReadinessReport | null
}): JSX.Element {
  const [mode, setMode] = useState<'paste' | 'pdf' | 'url'>('paste')
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [preview, setPreview] = useState('')
  const [documents, setDocuments] = useState<Document[]>([])
  const [listError, setListError] = useState('')
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<Match[]>([])
  const [searched, setSearched] = useState(false)
  const indexingBlocked = readiness?.semantic_ingestion_ready === false
  const urlBlocked = readiness?.url_ingestion_enabled === false

  async function refreshDocuments() {
    const list = await request<Document[]>('/documents')
    setDocuments(Array.isArray(list) ? list : [])
    setListError('')
  }
  useEffect(() => {
    void refreshDocuments().catch(() =>
      setListError('Saved documents could not be loaded. Check the server and reload the page.'),
    )
  }, [])

  async function action(kind: 'index' | 'save' | 'extract') {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      let path: string
      let options: RequestInit
      if (mode === 'pdf') {
        if (!file) throw new Error('Choose a document first.')
        const body = new FormData()
        body.append('file', file)
        if (title) body.append('title', title)
        options = { method: 'POST', body }
        path =
          kind === 'extract'
            ? '/documents/extract'
            : kind === 'save'
              ? '/documents/upload'
              : file.name.toLowerCase().endsWith('.pdf')
                ? '/ingest/pdf'
                : '/documents/upload'
      } else {
        if (!text.trim()) throw new Error('Enter knowledge text or a URL first.')
        path =
          mode === 'url' ? '/ingest/url' : kind === 'save' ? '/documents/paste' : '/ingest/paste'
        options = post(mode === 'url' ? { url: text } : { title: title || null, text })
      }
      let result = await request<{
        id?: string
        text?: string
        pages?: number
        chunks: number
        duplicate?: boolean
      }>(path, options)
      if (kind === 'index' && path === '/documents/upload' && result.id) {
        result = await request(`/documents/${result.id}/index`, post())
      }
      if (kind === 'extract') {
        setPreview(result.text ?? '')
        setMessage(`Extracted ${result.pages} page(s) locally. No Azure request was made.`)
      } else if (kind === 'save') {
        setMessage(
          result.duplicate
            ? 'This document is already saved locally.'
            : `${result.chunks} chunks saved locally. Keyword search is available; semantic indexing is separate.`,
        )
        await refreshDocuments()
      } else {
        setMessage(
          result.duplicate
            ? 'This content is already in knowledge.'
            : `${result.chunks} knowledge chunks ingested.`,
        )
      }
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function indexSaved(id: string) {
    setBusy(true)
    setError('')
    try {
      const result = await request<{ chunks: number; duplicate: boolean }>(
        `/documents/${id}/index`,
        post(),
      )
      setMessage(
        result.duplicate
          ? 'This content is already in semantic knowledge.'
          : `${result.chunks} chunks indexed for semantic retrieval.`,
      )
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }
  async function search(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await request<{ items: Match[] }>(
        `/knowledge/search?question=${encodeURIComponent(query)}`,
      )
      setMatches(result.items)
      setSearched(true)
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>Knowledge ingestion</h2>
      {listError && (
        <p className="muted" role="status">
          {listError}
        </p>
      )}
      <p className="muted">
        Save and search documents locally. Azure embeddings are required for semantic indexing.
      </p>
      <div className="tabs" role="group" aria-label="Ingestion method">
        {(['paste', 'pdf', 'url'] as const).map((item) => (
          <button
            key={item}
            type="button"
            disabled={busy}
            aria-pressed={mode === item}
            onClick={() => {
              setMode(item)
              setText('')
              setPreview('')
              setMessage('')
              setError('')
            }}
          >
            {item === 'pdf' ? 'PDF' : item === 'url' ? 'URL' : 'Paste'}
          </button>
        ))}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void action('index')
        }}
      >
        {mode !== 'url' && (
          <>
            <label htmlFor="title">Document title</label>
            <input id="title" value={title} onChange={(event) => setTitle(event.target.value)} />
          </>
        )}
        {mode === 'pdf' ? (
          <>
            <label htmlFor="pdf">PDF document</label>
            <input
              id="pdf"
              type="file"
              accept="application/pdf,.txt,.md"
              required
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null)
                setPreview('')
                setMessage('')
              }}
            />
            <p className="muted">
              PDF, UTF-8 .txt or .md; up to 5 MB. PDFs: up to 20 pages.
              {readiness?.local_ocr_available === false &&
                ' Scanned PDFs require local Poppler and Tesseract OCR.'}
            </p>
          </>
        ) : (
          <>
            <label htmlFor="knowledge">
              {mode === 'url' ? 'Public page URL' : 'Knowledge text'}
            </label>
            {mode === 'url' ? (
              <input
                id="knowledge"
                type="url"
                required
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            ) : (
              <textarea
                id="knowledge"
                rows={4}
                required
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            )}
          </>
        )}
        <button
          className="secondary"
          disabled={busy || indexingBlocked || (mode === 'url' && urlBlocked)}
        >
          {busy ? 'Working…' : 'Add to knowledge'}
        </button>
        {indexingBlocked && <p className="muted">{readiness?.semantic_ingestion_blocker}</p>}
        {mode === 'url' && urlBlocked && (
          <p className="muted">
            URL ingestion is disabled by the configured source or live acceptance policy.
          </p>
        )}
        {mode !== 'url' && (
          <button
            type="button"
            className="secondary"
            disabled={busy || (mode === 'pdf' ? !file : !text.trim())}
            onClick={() => void action('save')}
          >
            Save document locally
          </button>
        )}
        {mode === 'pdf' && (
          <button
            type="button"
            className="secondary"
            disabled={busy || !file}
            onClick={() => void action('extract')}
          >
            Extract text
          </button>
        )}
      </form>
      {error && <p role="alert">{error}</p>}
      <p role="status" className="muted">
        {message}
      </p>
      {preview && (
        <details open>
          <summary>Extracted text</summary>
          <pre className="document-preview">{preview}</pre>
        </details>
      )}
      {documents.length > 0 && (
        <>
          <h3>Locally saved documents</h3>
          <ul>
            {documents.map((document) => (
              <li key={document.id}>
                <strong>{document.title}</strong> · {document.pages} page(s)
                <button
                  type="button"
                  disabled={busy || indexingBlocked}
                  onClick={() => void indexSaved(document.id)}
                >
                  Index {document.title}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      <h3>Search local knowledge</h3>
      <p className="muted">
        Keyword matches with source citations. These are document excerpts, not an AI answer.
      </p>
      <form onSubmit={search}>
        <label htmlFor="knowledge-query">Knowledge search</label>
        <input
          id="knowledge-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          required
          maxLength={2000}
        />
        <button className="secondary" disabled={busy || !query.trim()}>
          Search knowledge
        </button>
      </form>
      {searched && !matches.length && (
        <p role="status">No matching excerpts found. Try terms used in the document.</p>
      )}
      <div aria-label="Knowledge search results">
        {matches.map((match) => (
          <article key={match.id}>
            <strong>{match.metadata.title ?? 'Untitled'}</strong>
            <p>{match.text}</p>
            <small>
              Source: {match.id} ·{' '}
              {match.storage === 'chroma' ? 'Indexed document' : 'Local document'}
            </small>
          </article>
        ))}
      </div>
    </section>
  )
}
