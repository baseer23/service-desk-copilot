import { useEffect, useRef, useState } from 'react'
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

interface DocumentStatus {
  id: string
  title?: string
  status?: string
  phase?: string
  extraction_method?: string
  local_index_ready?: boolean
  graph_link_status?: string
  graph_retry_terminal?: boolean
  semantic_graph_status?: string
  semantic_graph_retryable?: boolean
  error?: string
  processing_error_code?: string
  processing_error_reason?: string
  failed_stage?: string
  graph_last_error?: string
}

function processingSummary(document: DocumentStatus): { text: string; terminal: boolean } {
  const title = document.title ? `${document.title}\n` : ''
  if (document.status === 'error' || document.status === 'failed') {
    const reason = document.processing_error_reason ?? document.error ?? 'See server diagnostics.'
    const stage = document.failed_stage ? ` (${document.failed_stage.replace(/_/g, ' ')})` : ''
    const code = document.processing_error_code ? ` [${document.processing_error_code}]` : ''
    return { text: `${title}Processing failed${stage}: ${reason}${code}`, terminal: true }
  }
  if (!document.local_index_ready) {
    const stages: Record<string, string> = {
      queued: 'Queued', waiting: 'Queued', extracting: 'Azure extraction: Processing',
      structuring: 'Structuring', embedding: 'Embedding',
    }
    return { text: `${title}${stages[document.status ?? ''] ?? 'Saved'}`, terminal: false }
  }
  const local = 'Local indexing: Completed'
  if (document.graph_link_status !== 'linked') {
    const failed = document.status === 'graph_pending' && document.graph_last_error
    return {
      text: `${title}${local}\nNeo4j structural graph: ${
        document.graph_retry_terminal ? 'Failed' : failed ? 'Pending — retry scheduled' : 'Linking'
      }`,
      terminal: document.graph_retry_terminal === true,
    }
  }
  const semantic = document.semantic_graph_status ?? 'pending'
  if (semantic === 'linked') {
    return { text: `${title}${local}\nNeo4j structural graph: Linked\nSemantic understanding: Completed\nKnowledge ready`, terminal: true }
  }
  if (semantic === 'partial') {
    return { text: `${title}${local}\nNeo4j structural graph: Linked\nSemantic understanding: Partial`, terminal: true }
  }
  if (semantic === 'disabled') {
    return { text: `${title}${local}\nNeo4j structural graph: Linked\nSemantic understanding: Disabled`, terminal: true }
  }
  if (semantic === 'failed' && !document.semantic_graph_retryable) {
    return { text: `${title}${local}\nNeo4j structural graph: Linked\nSemantic understanding: Failed`, terminal: true }
  }
  const semanticStages: Record<string, string> = {
    pending: 'Queued', understanding: 'Processing', discovering: 'Entity discovery',
    placing: 'Entity placement', failed: 'Retry scheduled',
  }
  return {
    text: `${title}${local}\nNeo4j structural graph: Linked\nSemantic understanding: ${semanticStages[semantic] ?? 'Processing'}`,
    terminal: false,
  }
}

function normalizePublicUrl(value: string): string {
  const candidate = value.trim()
  if (!candidate) throw new Error('Enter a public website address.')
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(candidate)
  if (hasScheme && !/^https?:\/\//i.test(candidate)) {
    throw new Error('Use an HTTP or HTTPS website address.')
  }
  if (!hasScheme && !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?::\d{1,5})?(?:[/?#].*)?$/i.test(candidate)) {
    throw new Error('Enter a valid public domain, such as ngenuity.ca.')
  }
  let parsed: URL
  try {
    parsed = new URL(hasScheme ? candidate : `https://${candidate}`)
  } catch {
    throw new Error('Enter a valid HTTP or HTTPS website address.')
  }
  if (!parsed.hostname || parsed.username || parsed.password) {
    throw new Error('Enter a public website address without embedded credentials.')
  }
  return parsed.toString()
}

function Switch({ label, checked, disabled, available, onChange }: {
  label: string
  checked: boolean
  disabled: boolean
  available?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className={`knowledge-switch ${available === false ? 'unavailable' : ''}`}>
      <span>{label}<small>{available === false ? 'Unavailable' : available === undefined ? 'Checking' : checked ? 'ON' : 'OFF'}</small></span>
      <input type="checkbox" role="switch" aria-label={label} checked={checked}
        disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      <i aria-hidden="true" />
    </label>
  )
}

export default function KnowledgePanel({
  readiness,
  readinessChecking = false,
  readinessError = null,
}: {
  readiness: ReadinessReport | null
  readinessChecking?: boolean
  readinessError?: string | null
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
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const autoGraph = readiness?.automatic_graph_ingestion_enabled === true
  const azureAvailable = readiness?.azure_document_intelligence_available
  const neo4jReadAvailable = readiness?.neo4j_read_available
  const hybridAvailable = readiness?.structured_ingestion_available
  const indexingBlocked = readiness?.semantic_ingestion_ready === false
  const urlDisabledByConfiguration = readiness?.url_ingestion_enabled === false
  const urlReadinessUnavailable = readiness === null
  const urlBlocked = urlDisabledByConfiguration || urlReadinessUnavailable

  useEffect(() => {
    if (!activeDocumentId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const status = await request<DocumentStatus>(`/documents/${activeDocumentId}/status`)
        if (cancelled) return
        const summary = processingSummary(status)
        setMessage(summary.text)
        if (summary.terminal) setActiveDocumentId(null)
        else timer = setTimeout(() => void poll(), 750)
      } catch (caught) {
        if (!cancelled) {
          setError((caught as Error).message)
          setActiveDocumentId(null)
        }
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [activeDocumentId])

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
        body.append('ingestion_mode', 'structured_graph')
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
        if (mode === 'url' && readiness?.url_ingestion_enabled !== true) {
          throw new Error(urlDisabledByConfiguration
            ? 'Web page uploads are disabled by workspace configuration.'
            : 'DeskMate cannot verify URL-ingestion readiness. Check the backend connection and retry.')
        }
        path =
          mode === 'url' ? '/ingest/url' : kind === 'save' || readiness?.managed_documents ? '/documents/paste' : '/ingest/paste'
        const content = mode === 'url' ? normalizePublicUrl(text) : text
        if (mode === 'url') setText(content)
        options = post(mode === 'url' ? { url: content } : { title: title || null, text: content })
      }
      let result = await request<{
        id?: string
        text?: string
        pages?: number
        chunks: number
        duplicate?: boolean
        status?: string
        method?: string
        graph_nodes?: number
        graph_relationships?: number
        local_index_ready?: boolean
        graph_link_status?: 'linked' | 'pending' | 'not_configured'
        semantic_graph_status?: string
        failed_pages?: number
      }>(path, options)
      if (result.id && (path === '/documents/upload' || path === '/documents/paste')) {
        setActiveDocumentId(result.id)
      }
      if (kind === 'index' && !readiness?.managed_documents && path === '/documents/upload' && result.id) {
        result = await request(`/documents/${result.id}/index`, post())
      }
      if (kind === 'extract') {
        setPreview(result.text ?? '')
        setMessage(autoGraph
          ? `${result.method === 'document_intelligence' ? 'Azure Document Intelligence' : 'Local text fallback'} extracted ${result.pages} page(s). Neo4j linked ${result.graph_nodes ?? 0} nodes and ${result.graph_relationships ?? 0} relationships; the original and extracted text were not saved in DeskMate.`
          : `Text preview is ready (${result.pages} page(s)).`)
      } else if (mode === 'url' && result.local_index_ready) {
        const indexed = `${result.pages ?? 0} public page(s) were added to local knowledge.`
        const failed = result.failed_pages ? ` ${result.failed_pages} page(s) could not be indexed.` : ''
        const graph = result.graph_link_status === 'linked'
          ? result.semantic_graph_status === 'linked'
            ? ' Neo4j semantic linking is complete.'
            : ' Neo4j references are linked; semantic enrichment is pending.'
          : ' Local knowledge is ready; Neo4j linking remains pending.'
        setMessage(indexed + failed + graph)
      } else if (result.status && result.status !== 'ready') {
        const stage: Record<string, string> = {
          queued: 'Extracting', extracting: 'Extracting', structuring: 'Structuring',
          embedding: 'Embedding', linking: 'Linking', graph_pending: 'Graph linking unavailable',
        }
        setMessage(
          result.status === 'graph_pending'
            ? 'Local indexing is ready, but Neo4j linking is pending. Configure the authorized ingestion connection and retry linking.'
            : `${stage[result.status] ?? 'Processing'}`,
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
            : mode === 'url'
              ? `${result.pages ?? 0} public page(s) were added to knowledge.`
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
        <Switch label="Azure Document Intelligence" checked={azureAvailable === true && (autoGraph || azureDi)}
          available={azureAvailable}
          disabled={autoGraph || busy || azureAvailable !== true}
          onChange={setAzureDi} />
        <Switch label="Neo4j Read" checked={neo4jReadAvailable === true && neo4jRead}
          available={neo4jReadAvailable}
          disabled={busy || neo4jReadAvailable !== true}
          onChange={setNeo4jRead} />
        <Switch label="Local + Neo4j Hybrid" checked={hybridAvailable === true}
          available={hybridAvailable} disabled
          onChange={() => undefined} />
        <p className="muted knowledge-switch-note">
          {readiness?.azure_document_intelligence_available === false
            ? `${readiness.azure_document_intelligence_blocker ?? 'Azure Document Intelligence is unavailable; local document processing remains available.'} `
            : autoGraph
              ? 'Automatic ingestion is enabled: every file upload uses Azure Document Intelligence and writes reference-only nodes and links to Neo4j. Extraction previews do not save the original locally, but their graph references persist. Azure processing may incur charges. '
              : 'Azure processing runs only when you upload a document. '}
          {readiness?.neo4j_read_available === false
            ? 'Neo4j read is unavailable. '
            : 'Neo4j access is read-only. '}
          {readiness?.structured_ingestion_available === true
            ? 'Hybrid policy is always ON: saved content is indexed locally and linked to Neo4j.'
            : 'Hybrid policy remains ON. Local indexing continues and Neo4j links stay pending until the authorized writer is available.'}
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
                type="text"
                inputMode="url"
                placeholder="https://example.com or example.com"
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
          disabled={busy || (indexingBlocked && !readiness?.managed_documents) || (mode === 'url' && (urlBlocked || readinessChecking))}
        >
          {busy ? 'Working…' : 'Add to knowledge'}
        </button>
        {indexingBlocked && <p className="muted">Document processing is temporarily unavailable. You can still save documents and search available knowledge.</p>}
        {mode === 'url' && urlDisabledByConfiguration && (
          <p className="muted">
            {readiness?.url_ingestion_blocker ?? 'Web page uploads are disabled by workspace configuration.'} You can upload a document or paste its text.
          </p>
        )}
        {mode === 'url' && readinessChecking && <p className="muted">Checking whether web page uploads are available…</p>}
        {mode === 'url' && !readinessChecking && urlReadinessUnavailable && (
          <p className="muted">{readinessError
            ? `Readiness check failed: ${readinessError}`
            : 'The backend is unavailable, so web page upload readiness cannot be verified.'}</p>
        )}
        {mode === 'url' && readiness?.url_ingestion_enabled === true && !busy && (
          <p className="muted">Web page uploads are enabled. Public URL security and robots.txt rules still apply.</p>
        )}
        {mode === 'url' && busy && <p className="muted">Crawling and indexing a bounded set of public pages…</p>}
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
