import { FormEvent, useEffect, useState } from 'react'
import VoiceSupport from './VoiceSupport'
import KnowledgePanel from './KnowledgePanel'
import { post, request } from './api'
import { reasoningBlocker, useReadiness } from './readiness'
import { InvestigationTimeline, EvidenceList, timestamp } from './InvestigationPanels'

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
export default function App() {
  const { readiness, refresh, checking } = useReadiness()
  const blocker = reasoningBlocker(readiness)
  const [ticket, setTicket] = useState('')
  const [active, setActive] = useState<Case | null>(null)
  const [cases, setCases] = useState<Case[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')


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
  async function saveRequest() {
    if (!ticket.trim()) return
    setBusy(true)
    setError('')
    try { await display(await request<Case>('/cases', post({ summary: ticket.trim() }))) }
    catch (caught) { setError((caught as Error).message) }
    finally { setBusy(false) }
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
        <span className="runtime-label">{readiness?.runtime_mode==='isolated_demo' ? 'Isolated demo' : 'Case investigations'}</span>
      </header>
      {readiness?.runtime_mode === 'isolated_demo' && <div className="demo-banner runtime-banner"><strong>ISOLATED DEMO</strong><p>Responses and audio are simulated in this sample session.</p></div>}
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
              <button className="primary" disabled={busy || !ticket.trim() || (readiness !== null && !readiness.text_ready)}>
                {busy ? 'Investigating…' : 'Run DeskMate'}
              </button>
              {blocker && <p className="muted" role="status">{blocker}</p>}
              <button type="button" className="secondary" disabled={busy || !ticket.trim()} onClick={() => void saveRequest()}>Save request</button>
              <button type="button" className="secondary" disabled={checking || busy} onClick={() => void refresh()}>{checking ? 'Checking app status…' : 'Refresh app status'}</button>
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
          <KnowledgePanel readiness={readiness} />
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
                <InvestigationTimeline events={active.workflow==='knowledge' ? events.filter(event=>/^(search_|fetch_|ingest_|evaluate_sources$|verify$)/.test(event.tool ?? '')) : events} />
                {busy && (
                  <p className="muted" role="status">
                    Gathering evidence…
                  </p>
                )}
              </section>
              {active.status === 'new' && <section className="card">
                <h2>Saved request</h2>
                <p>Your request is saved. Automated investigation has not started.</p>
                <button className="primary" disabled={busy || !!blocker} onClick={() => void retry()}>Investigate saved case</button>
                {blocker && <p className="muted">{blocker}</p>}
              </section>}
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
                    <p>Evidence verification complete. Review the sources and uncertainty above.</p>
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
                      : active.status === 'new' ? 'Investigation has not started.' : 'Investigation is in progress.'}
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
                    <li key={t}>{t?.startsWith('search_') ? 'Search supporting information' : t?.startsWith('get_') ? 'Review supporting information' : 'Save supporting information'}</li>
                  ))}
                </ul>
                {active.workflow === 'knowledge' && (
                  <>
                    <h3>Request activity</h3>
                    <InvestigationTimeline events={events} />
                  </>
                )}
              </details>
              <section className="card">
                <h2>
                  Sources <span className="count">{active.evidence.length}</span>
                </h2>
                {!active.evidence.length && <p className="muted">No evidence collected yet.</p>}
                <EvidenceList evidence={active.evidence} />
              </section>
            </>
          )}
        </div>
      </main>
      <VoiceSupport onCaseCreated={openCase} readiness={readiness} />
      <footer>Evidence guides the next step. A recommendation does not confirm resolution.</footer>
    </div>
  )
}
