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

function Switch({ label, checked, disabled, onChange }: {
  label: string
  checked: boolean
  disabled: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className={`knowledge-switch ${disabled ? 'unavailable' : ''}`}>
      <span>{label}<small>{disabled ? 'Unavailable' : checked ? 'ON' : 'OFF'}</small></span>
      <input type="checkbox" role="switch" aria-label={label} checked={checked}
        disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <i aria-hidden="true" />
    </label>
  )
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
  const [azureDi, setAzureDi] = useState(true)
  const [neo4jRead, setNeo4jRead] = useState(true)
  const [hybrid, setHybrid] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const autoGraph = readiness?.automatic_graph_ingestion_enabled === true
  const indexingBlocked = readiness?.semantic_ingestion_ready === false
  const urlBlocked = readiness?.url_ingestion_enabled === false

  function chooseFile(selected: File | undefined) {
    if (!selected) return
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
        body.append('document_processing', autoGraph || azureDi ? 'azure_di' : 'local')
        body.append('ingestion_mode', autoGraph || hybrid ? 'structured_graph' : 'standard')
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
        setMessage(autoGraph
          ? `Azure Document Intelligence extracted ${result.pages} page(s). Structural references are saved in Neo4j; the original was not saved in DeskMate.`
          : `Text preview is ready (${result.pages} page(s)).`)
      } else if (result.status && result.status !== 'ready') {
        const stage: Record<string, string> = {
          queued: 'Extracting', extracting: 'Extracting', structuring: 'Structuring',
          embedding: 'Embedding', linking: 'Linking', graph_pending: 'Graph linking unavailable',
        }
        setMessage(
          result.status === 'graph_pending'
            ? 'Local indexing is ready, but Neo4j linking is pending. Configure the authorized ingestion connection and retry linking.'
            : `${stage[result.status] ?? 'Processing'} → Ready`,
        )
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
      <div className="knowledge-switches" aria-label="Knowledge processing options">
        <Switch label="Azure Document Intelligence" checked={autoGraph || azureDi}
          disabled={autoGraph || busy || readiness?.azure_document_intelligence_available === false}
          onChange={setAzureDi} />
        <Switch label="Neo4j Read" checked={neo4jRead}
          disabled={busy || readiness?.neo4j_read_available === false}
          onChange={setNeo4jRead} />
        <Switch label="Azure DI + Neo4j Hybrid" checked={autoGraph || hybrid}
          disabled={autoGraph || busy || !azureDi || !neo4jRead || readiness?.structured_ingestion_available !== true}
          onChange={setHybrid} />
        <p className="muted knowledge-switch-note">
          {autoGraph
            ? 'Automatic ingestion is enabled: every file upload uses Azure Document Intelligence and writes reference-only nodes and links to Neo4j. Extraction previews do not save the original locally, but their graph references persist. Azure processing may incur charges. '
            : readiness?.azure_document_intelligence_available === false
              ? 'Azure Document Intelligence is unavailable; local document processing remains available. '
              : 'Azure processing runs only when you upload a document. '}
          {readiness?.neo4j_read_available === false
            ? 'Neo4j read is unavailable. '
            : 'Neo4j access is read-only. '}
          {autoGraph
            ? readiness?.structured_ingestion_available === true
              ? 'Saved uploads are also indexed into local ChromaDB.'
              : 'Neo4j ingestion writer is not ready: uploads will be blocked until configured.'
            : readiness?.structured_ingestion_available !== true
              ? 'Hybrid ingestion is unavailable until the backend writer is verified.'
              : hybrid
                ? 'Hybrid is ON: uploads use Azure extraction, local ChromaDB, and Neo4j references.'
                : 'Hybrid is OFF: uploads remain local and do not write graph references.'}
        </p>
      </div>
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
              {readiness?.managed_documents ? 'PDF, Word, images or UTF-8 text. Large PDFs are processed in resumable batches.' : 'PDF, UTF-8 .txt or .md.'}
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
        {busy && mode === 'pdf' ? 'Uploading and processing…' : message}
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
