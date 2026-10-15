import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { post, request } from './api'
import { type ReadinessReport } from './readiness'

interface Match {
  id: string
  text: string
  storage: string
  metadata: {
    title?: string
    doc_id?: string
    page?: number
    source?: string
    extraction_method?: string
    table_count?: number
  }
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
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState<Match[]>([])
  const [searched, setSearched] = useState(false)
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const indexingBlocked = readiness?.semantic_ingestion_ready === false
  const urlBlocked = readiness?.url_ingestion_enabled === false

  function chooseFile(selected: File | undefined) {
    if (!selected) return
    if (selected.size > 5 * 1024 * 1024) { setError('Choose a document smaller than 5 MB.'); return }
    setFile(selected)
    setMode('pdf')
    setPreview('')
    setMessage('')
    setError('')
  }

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
          readiness?.managed_documents && kind !== 'extract' ? '/documents/upload' :
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
          mode === 'url' ? '/ingest/url' : kind === 'save' || readiness?.managed_documents ? '/documents/paste' : '/ingest/paste'
        options = post(mode === 'url' ? { url: text } : { title: title || null, text })
      }
      let result = await request<{
        id?: string
        text?: string
        pages?: number
        chunks: number
        duplicate?: boolean
        status?: string
      }>(path, options)
      if (kind === 'index' && !readiness?.managed_documents && path === '/documents/upload' && result.id) {
        result = await request(`/documents/${result.id}/index`, post())
      }
      if (kind === 'extract') {
        setPreview(result.text ?? '')
        setMessage(`Text preview is ready (${result.pages} page(s)).`)
      } else if (result.status && result.status !== 'ready') {
        setMessage('Your document is uploaded. It will appear in knowledge when processing is complete.')
      } else if (kind === 'save') {
        setMessage(
          result.duplicate
            ? 'This document is already saved.'
            : 'Your document is saved. Search available knowledge or return when processing is complete.',
        )
      } else {
        setMessage(
          result.duplicate
            ? 'This content is already in knowledge.'
            : 'Your document was added to knowledge.',
        )
      }
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
    <section className="card knowledge-card">
      <h2>Documents and knowledge</h2>
      <div className={`document-drop ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files[0]) }}>
        <svg aria-hidden="true" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5" /></svg>
        <h3>{file ? file.name : 'Give your answers a source'}</h3><p>Drag a document here, or choose a file.</p>
        <button type="button" className="button button-neutral" disabled={busy} onClick={() => picker.current?.click()}>Choose document</button>
        <p className="muted">Share only documents you’re authorized to use.</p>
      </div>
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
        <div hidden={mode !== 'pdf'}>
            <label htmlFor="pdf">PDF document</label>
            <input
              ref={picker}
              id="pdf"
              type="file"
              accept={readiness?.managed_documents ? 'application/pdf,.txt,.md,.docx,.png,.jpg,.jpeg,.tiff' : 'application/pdf,.txt,.md'}
              required={mode === 'pdf' && !file}
              onChange={(event) => chooseFile(event.target.files?.[0])}
            />
            <p className="muted">
              {readiness?.managed_documents ? 'PDF, Word, images or UTF-8 text; up to 5 MB. PDFs: up to 100 pages.' : 'PDF, UTF-8 .txt or .md; up to 5 MB. PDFs: up to 20 pages.'}
              {readiness?.local_ocr_available === false &&
                ' Text preview may be unavailable for scanned documents. Upload them for processing.'}
            </p>
          </div>
        {mode !== 'pdf' && (
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
          disabled={busy || (indexingBlocked && !readiness?.managed_documents) || (mode === 'url' && urlBlocked)}
        >
          {busy ? 'Working…' : 'Add to knowledge'}
        </button>
        {indexingBlocked && <p className="muted">Document processing is temporarily unavailable. You can still save documents and search available knowledge.</p>}
        {mode === 'url' && urlBlocked && (
          <p className="muted">
            Web page uploads are currently unavailable. You can upload a document or paste its text.
          </p>
        )}
        {mode !== 'url' && (
          <button
            type="button"
            className="secondary"
            disabled={busy || (mode === 'pdf' ? !file : !text.trim())}
            onClick={() => void action('save')}
          >
            Save document
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
      <h3>Search knowledge</h3>
      <p className="muted">
        Matching document excerpts with source references. Open an investigation for a supported answer.
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
              Source: {match.metadata.title ?? 'Untitled'} · Document excerpt{match.metadata.page ? ` · Page ${match.metadata.page}` : ''}
              {match.metadata.extraction_method === 'document_intelligence' ? ' · Azure Document Intelligence layout' : ''}
              {match.metadata.table_count ? ` · ${match.metadata.table_count} table${match.metadata.table_count === 1 ? '' : 's'}` : ''}
              {match.metadata.doc_id && readiness?.managed_documents && <a href={`/documents/${encodeURIComponent(match.metadata.doc_id)}/download`}>View source</a>}
            </small>
          </article>
        ))}
      </div>
    </section>
  )
}
