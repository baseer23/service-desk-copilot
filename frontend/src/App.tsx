import { FormEvent, useEffect, useState } from 'react'

type Fact = {
  subject: string
  predicate: string
  value: string
  source: string
  source_system: string
  observed_at: string
  case_id: string
}
type Evidence = {
  tool: string
  source: string
  source_system: string
  observed_at: string
  source_type?: string
  url?: string | null
  content: unknown
}
export type Case = {
  case_id: string
  thread_id: string
  ticket: { summary: string }
  status: string
  pending_question: string | null
  likely_issue: string | null
  workflow?: string
  transient?: boolean
  contradictions?: string[]
  verification?: {
    supported_claims?: string[]
    unsupported_claims?: string[]
    justification?: string
  }
  final_response?: {
    answer?: string
    troubleshooting_steps?: string[]
    questions_remaining?: string[]
    sources?: string[]
    escalation_required?: boolean
  }
  hypotheses: (string | { claim: string; confidence: number; status: string })[]
  confirmed_facts: Fact[]
  remembered_facts: Fact[]
  inferences: string[]
  recommended_next_action: string | null
  confidence: number
  uncertainty: string[]
  evidence: Evidence[]
  final_answer: string | null
}
type Event = {
  id: number
  tool: string | null
  source: string
  content: string
  created_at: string
}
type AskResponse = {
  request_id: string
  case_id: string | null
  result: Omit<Case, 'case_id' | 'thread_id'>
  events: Event[]
}
const base = import.meta.env.VITE_API_BASE || ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(base + path, init)
  } catch {
    throw new Error('DeskMate could not connect to the server. Please try again.')
  }
  let body
  try {
    body = await response.json()
  } catch {
    throw new Error(
      response.ok
        ? 'DeskMate received an empty or invalid server response. Reload the page and try again.'
        : response.status >= 500
          ? `The DeskMate server is temporarily unavailable (HTTP ${response.status}). Please try again.`
          : `Request failed (HTTP ${response.status}). The server returned an empty or invalid response.`,
    )
  }
  if (!response.ok)
    throw Object.assign(
      new Error(
        typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status})`,
      ),
      { case_id: typeof body?.case_id === 'string' ? body.case_id : undefined },
    )
  return body as T
}
const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
})
const timestamp = (value: string) =>
  new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z').toLocaleString()
const readable = (value: unknown): string => {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return String(value ?? '')
  const row = value as Record<string, unknown>
  if (typeof row.text === 'string') return row.text
  return Object.entries(row)
    .filter(
      ([key, val]) =>
        !['metadata', 'source_system', 'observed_at', 'source'].includes(key) &&
        (typeof val === 'string' || typeof val === 'boolean' || typeof val === 'number'),
    )
    .map(([key, val]) => `${key.replace(/_/g, ' ')}: ${val}`)
    .join(' · ')
}

export default function App() {
  const [ticket, setTicket] = useState('')
  const [active, setActive] = useState<Case | null>(null)
  const [cases, setCases] = useState<Case[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ingestMode, setIngestMode] = useState<'paste' | 'pdf' | 'url'>('paste')
  const [knowledge, setKnowledge] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [ingestMessage, setIngestMessage] = useState('')
  const [ingesting, setIngesting] = useState(false)

  useEffect(() => {
    request<Case[]>('/cases')
      .then(setCases)
      .catch((e) => setError(e.message))
  }, [])

  async function display(current: Case) {
    setActive(current)
    const [timeline, list] = await Promise.all([
      request<Event[]>(`/cases/${current.case_id}/events`),
      request<Case[]>('/cases'),
    ])
    setEvents(timeline)
    setCases(list)
  }
  async function displayRequest(response: AskResponse) {
    setActive({
      ...response.result,
      case_id: response.case_id || response.request_id,
      thread_id: response.request_id,
      transient: !response.case_id,
    })
    setEvents(response.events)
    setCases(await request<Case[]>('/cases'))
  }
  async function start(event: FormEvent) {
    event.preventDefault()
    if (!ticket.trim()) return
    setBusy(true)
    setError('')
    setAnswer('')
    setEvents([])
    try {
      await displayRequest(await request<AskResponse>('/ask', post({ summary: ticket.trim() })))
    } catch (e) {
      setError((e as Error).message)
      const id = (e as Error & { case_id?: string }).case_id
      if (id) await refreshAfterFailure(id)
    } finally {
      setBusy(false)
    }
  }
  async function resume(event: FormEvent) {
    event.preventDefault()
    if (!active || !answer.trim()) return
    setBusy(true)
    setError('')
    try {
      if (active.transient) {
        await displayRequest(
          await request<AskResponse>(
            `/requests/${active.case_id}/resume`,
            post({ answer: answer.trim() }),
          ),
        )
      } else
        await display(
          await request<Case>(`/cases/${active.case_id}/resume`, post({ answer: answer.trim() })),
        )
      setAnswer('')
    } catch (e) {
      setError((e as Error).message)
      await refreshAfterFailure(active.case_id)
    } finally {
      setBusy(false)
    }
  }
  async function refreshAfterFailure(id: string) {
    try {
      await display(await request<Case>(`/cases/${id}`))
    } catch {
      // Keep the original actionable error when the API itself is unavailable.
    }
  }
  async function retry() {
    if (!active) return
    setBusy(true)
    setError('')
    try {
      await display(await request<Case>(`/cases/${active.case_id}/investigate`, post()))
    } catch (e) {
      setError((e as Error).message)
      await refreshAfterFailure(active.case_id)
    } finally {
      setBusy(false)
    }
  }
  async function openCase(id: string) {
    setBusy(true)
    setError('')
    setAnswer('')
    try {
      await display(await request<Case>(`/cases/${id}`))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  async function ingest(event: FormEvent) {
    event.preventDefault()
    setIngesting(true)
    setIngestMessage('')
    setError('')
    try {
      let options: RequestInit
      if (ingestMode === 'pdf') {
        if (!file) throw new Error('Choose a PDF first.')
        const data = new FormData()
        data.append('file', file)
        if (title) data.append('title', title)
        options = { method: 'POST', body: data }
      } else
        options = post(
          ingestMode === 'paste' ? { title: title || null, text: knowledge } : { url: knowledge },
        )
      const result = await request<{ chunks: number; duplicate: boolean }>(
        `/ingest/${ingestMode}`,
        options,
      )
      setIngestMessage(
        result.duplicate
          ? 'This content is already in knowledge.'
          : `${result.chunks} knowledge chunks ingested.`,
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setIngesting(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="masthead">
        <div className="brand-mark" aria-hidden="true">
          D
        </div>
        <div>
          <h1>DeskMate</h1>
          <p>Service Desk Investigation Assistant</p>
        </div>
        <span className="runtime-label">Case investigations</span>
      </header>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      <main>
        <aside className="case-panel">
          <section className="card">
            <span className="eyebrow">START HERE</span>
            <h2>New Request</h2>
            <form onSubmit={start}>
              <label htmlFor="ticket">Question or ticket</label>
              <textarea
                id="ticket"
                rows={7}
                placeholder="Ask about a document, or describe an incident…"
                value={ticket}
                onChange={(e) => setTicket(e.target.value)}
                required
              />
              <button className="primary" disabled={busy || !ticket.trim()}>
                {busy ? 'Investigating…' : 'Run DeskMate'}
              </button>
            </form>
          </section>
          <section className="card">
            <h2>Cases</h2>
            {cases.length === 0 ? (
              <p className="muted">Your investigations will appear here.</p>
            ) : (
              <ul className="case-list">
                {cases.map((c) => (
                  <li key={c.case_id}>
                    <button
                      className={active?.case_id === c.case_id ? 'selected' : ''}
                      onClick={() => openCase(c.case_id)}
                      disabled={busy}
                    >
                      <span>{c.ticket.summary}</span>
                      <small>{c.status.replace(/_/g, ' ')}</small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <h2>Knowledge ingestion</h2>
            <div className="tabs" role="group" aria-label="Ingestion method">
              {(['paste', 'pdf', 'url'] as const).map((mode) => (
                <button
                  key={mode}
                  aria-pressed={ingestMode === mode}
                  onClick={() => {
                    setIngestMode(mode)
                    setKnowledge('')
                    setIngestMessage('')
                  }}
                >
                  {mode === 'pdf' ? 'PDF' : mode === 'url' ? 'URL' : 'Paste'}
                </button>
              ))}
            </div>
            <form onSubmit={ingest}>
              {ingestMode !== 'url' && (
                <>
                  <label htmlFor="title">Document title</label>
                  <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} />
                </>
              )}
              {ingestMode === 'pdf' ? (
                <>
                  <label htmlFor="pdf">PDF document</label>
                  <input
                    id="pdf"
                    type="file"
                    accept="application/pdf"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    required
                  />
                </>
              ) : (
                <>
                  <label htmlFor="knowledge">
                    {ingestMode === 'url' ? 'Public page URL' : 'Knowledge text'}
                  </label>
                  {ingestMode === 'url' ? (
                    <input
                      id="knowledge"
                      type="url"
                      value={knowledge}
                      onChange={(e) => setKnowledge(e.target.value)}
                      required
                    />
                  ) : (
                    <textarea
                      id="knowledge"
                      rows={4}
                      value={knowledge}
                      onChange={(e) => setKnowledge(e.target.value)}
                      required
                    />
                  )}
                </>
              )}
              <button className="secondary" disabled={ingesting}>
                {ingesting ? 'Ingesting…' : 'Add to knowledge'}
              </button>
              <p role="status" className="muted">
                {ingestMessage}
              </p>
            </form>
          </section>
        </aside>
        <div className="investigation-panel">
          {!active ? (
            <section className="empty-state">
              <div aria-hidden="true">◎</div>
              <h2>Start with a question or an incident.</h2>
              <p>
                DeskMate chooses a knowledge or investigation workflow, gathers evidence, and
                verifies its answer.
              </p>
            </section>
          ) : (
            <>
              <div className="case-heading">
                <div>
                  <span className="eyebrow">
                    {active.workflow === 'knowledge' ? 'KNOWLEDGE REQUEST' : 'CURRENT CASE'}
                  </span>
                  <p className="case-id" data-testid="case-id">
                    {active.case_id}
                  </p>
                </div>
                <span className={`status-pill ${active.status}`} role="status">
                  {active.status.replace(/_/g, ' ')}
                </span>
              </div>
              <p className="ticket-summary">{active.ticket.summary}</p>
              {active.workflow === 'knowledge' && (
                <section className="card knowledge-answer">
                  <h2>Answer</h2>
                  <p>
                    {active.final_response?.answer ||
                      active.final_answer ||
                      (active.status === 'insufficient_information'
                        ? 'The available evidence does not support an answer yet.'
                        : 'Gathering and verifying supporting documents…')}
                  </p>
                </section>
              )}
              <section className="card">
                <h2>
                  {active.workflow === 'knowledge' ? 'Research summary' : 'Investigation Timeline'}
                </h2>
                <ol className="timeline">
                  {(active.workflow === 'knowledge'
                    ? events.filter((e) =>
                        /^(search_|fetch_|ingest_|evaluate_sources$|verify$)/.test(e.tool || ''),
                      )
                    : events
                  ).map((e) => (
                    <li key={e.id}>
                      <span className="event-dot" aria-hidden="true">
                        {e.tool === 'error' ? '!' : '✓'}
                      </span>
                      <div>
                        <p>{e.content}</p>
                        <small>
                          {e.source} · {timestamp(e.created_at)}
                        </small>
                      </div>
                    </li>
                  ))}
                </ol>
                {busy && (
                  <p className="muted" role="status">
                    Gathering evidence…
                  </p>
                )}
              </section>
              {active.status === 'error' && (
                <section className="card">
                  <h2>Investigation interrupted by an error</h2>
                  <p className="muted">
                    Retry continues this case from its saved investigation state.
                  </p>
                  <button className="primary" disabled={busy} onClick={retry}>
                    Retry Investigation
                  </button>
                </section>
              )}
              {active.status === 'needs_input' && (
                <section className="card human-input">
                  <span className="eyebrow">YOUR INPUT IS NEEDED</span>
                  <h2>Human Input</h2>
                  <p className="question">{active.pending_question}</p>
                  <form onSubmit={resume}>
                    <label htmlFor="answer">Your answer</label>
                    <input
                      id="answer"
                      value={answer}
                      onChange={(e) => setAnswer(e.target.value)}
                      autoComplete="off"
                      required
                    />
                    <button className="primary" disabled={busy || !answer.trim()}>
                      Continue Investigation
                    </button>
                  </form>
                </section>
              )}
              <section className="card assessment">
                <h2>
                  {active.workflow === 'knowledge' ? 'Evidence assessment' : 'Current Assessment'}
                </h2>
                <dl>
                  {active.workflow !== 'knowledge' && (
                    <>
                      <dt>Likely issue</dt>
                      <dd>{active.likely_issue || 'Still gathering information'}</dd>
                      <dt>Next best action</dt>
                      <dd className="next-action">
                        {active.recommended_next_action || 'Await the next investigation step.'}
                      </dd>
                    </>
                  )}
                  <dt>Confidence</dt>
                  <dd>
                    {Math.round(active.confidence * 100)}%{' '}
                    <small className="muted">
                      {active.workflow === 'knowledge'
                        ? 'Support for the answer.'
                        : 'Evidence support; resolution is unverified.'}
                    </small>
                  </dd>
                </dl>
                {(active.workflow !== 'knowledge' || active.hypotheses.length > 0) && (
                  <>
                    <h3>Hypotheses</h3>
                    {active.hypotheses.length ? (
                      <ul>
                        {active.hypotheses.map((h, i) => (
                          <li key={i}>{typeof h === 'string' ? h : h.claim}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="muted">No supported hypothesis yet.</p>
                    )}
                  </>
                )}
                {(active.workflow !== 'knowledge' || active.confirmed_facts.length > 0) && (
                  <>
                    <h3>Confirmed facts</h3>
                    {active.confirmed_facts.length ? (
                      <ul>
                        {active.confirmed_facts.map((f, i) => (
                          <li key={i}>
                            {f.subject} · {f.predicate.replace(/_/g, ' ')}:{' '}
                            <strong>{f.value}</strong>
                            <small className="fact-source">
                              {f.source} · {timestamp(f.observed_at)}
                            </small>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="muted">No human-confirmed facts in this case yet.</p>
                    )}
                  </>
                )}
                {active.remembered_facts.length > 0 && (
                  <div className="remembered">
                    <h3>Remembered facts from previous cases</h3>
                    <ul>
                      {active.remembered_facts.map((f, i) => (
                        <li key={i}>
                          {f.subject} · {f.predicate.replace(/_/g, ' ')}: <strong>{f.value}</strong>
                          <small className="fact-source">
                            {f.source} · {timestamp(f.observed_at)} · case {f.case_id}
                          </small>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {(active.workflow !== 'knowledge' || active.inferences.length > 0) && (
                  <>
                    <h3>Inferences</h3>
                    {active.inferences.length ? (
                      <ul>
                        {active.inferences.map((h, i) => (
                          <li key={i}>{h}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="muted">No additional inference.</p>
                    )}
                  </>
                )}
                <h3>Contradictions</h3>
                {(active.contradictions || []).length ? (
                  <ul>
                    {active.contradictions!.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">No conflicting evidence identified.</p>
                )}
                {active.verification?.justification && (
                  <details>
                    <summary>Evidence verification</summary>
                    <p>{active.verification.justification}</p>
                  </details>
                )}
                {active.final_response?.troubleshooting_steps?.length ? (
                  <>
                    <h3>Troubleshooting steps</h3>
                    <ol>
                      {active.final_response.troubleshooting_steps.map((step, i) => (
                        <li key={i}>{step}</li>
                      ))}
                    </ol>
                  </>
                ) : null}
                <h3>Uncertainty</h3>
                {active.uncertainty.length ? (
                  <ul>
                    {active.uncertainty.map((u, i) => (
                      <li key={i}>{u}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">
                    {active.status === 'assessment_ready'
                      ? 'No additional uncertainty reported.'
                      : 'Investigation is in progress.'}
                  </p>
                )}
              </section>
              <details className="card agent-activity">
                <summary>Agent activity</summary>
                <p>Tools used</p>
                <ul>
                  {Array.from(
                    new Set(
                      events
                        .map((e) => e.tool)
                        .filter((t) => t && /^(search_|get_|fetch_|ingest_)/.test(t)),
                    ),
                  ).map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
                {active.workflow === 'knowledge' && (
                  <>
                    <h3>Full request history</h3>
                    <ol className="timeline">
                      {events.map((e) => (
                        <li key={e.id}>
                          <div>
                            <p>{e.content}</p>
                            <small>
                              {e.source} · {timestamp(e.created_at)}
                            </small>
                          </div>
                        </li>
                      ))}
                    </ol>
                  </>
                )}
              </details>
              <section className="card">
                <h2>
                  Sources <span className="count">{active.evidence.length}</span>
                </h2>
                {!active.evidence.length && <p className="muted">No evidence collected yet.</p>}
                <div className="evidence-list">
                  {active.evidence.map((e, i) => (
                    <details key={i}>
                      <summary>
                        <span>
                          {e.tool.replace(/_/g, ' ')} · {e.source}
                        </span>
                        <span
                          className={`source-badge ${e.source_system === 'local_fixture' ? 'fixture' : ''}`}
                        >
                          {e.source_system === 'local_fixture' ? 'Fixture' : 'Real source'} ·{' '}
                          {e.source_system}
                        </span>
                      </summary>
                      <small>
                        {timestamp(e.observed_at)} · {e.tool}
                        {e.source_type ? ' · ' + e.source_type.replace(/_/g, ' ') : ''}
                      </small>
                      {e.url && /^https?:\/\//i.test(e.url) && (
                        <p>
                          <a href={e.url} target="_blank" rel="noreferrer">
                            Open source
                          </a>
                        </p>
                      )}
                      <p className="source-content">{readable(e.content)}</p>
                    </details>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </main>
      <footer>Evidence guides the next step. A recommendation does not confirm resolution.</footer>
    </div>
  )
}
