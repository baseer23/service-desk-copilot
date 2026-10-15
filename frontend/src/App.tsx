import { type FormEvent, useEffect, useRef, useState } from 'react'
import { ProviderSelector } from './ProviderSelector'
import InvestigationActivity from './InvestigationActivity'
import { streamInvestigation, type ActivityEvent } from './investigationStream'
import VoiceSupport from './VoiceSupport'
import KnowledgePanel from './KnowledgePanel'
import InvestigationHistory from './InvestigationHistory'
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
  origin?: 'user' | 'demo' | 'test' | 'unknown'
  resolution_status?: string
  chat_messages?: { role: string; content: string }[]
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
export default function App(): JSX.Element {
  // This custom data hook accepts no effect callback.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const { readiness, refresh, checking } = useReadiness()
  const blocker = reasoningBlocker(readiness)
  const [ticket, setTicket] = useState('')
  const [active, setActive] = useState<Case | null>(null)
  const [cases, setCases] = useState<Case[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [section, setSection] = useState('Chat')
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const investigation = useRef<AbortController | null>(null)
  useEffect(() => () => investigation.current?.abort(), [])

  async function investigate<T>(path: string, body?: unknown): Promise<T> {
    investigation.current?.abort()
    const controller = new AbortController()
    investigation.current = controller
    setActivity([])
    try {
      return await streamInvestigation<T>(path, body, event => setActivity(current => [...current, event]), controller.signal)
    } catch (error) {
      if (controller.signal.aborted) {
        setActivity(current => [...current, { sequence: (current[current.length - 1]?.sequence ?? 0) + 1, kind: 'workflow', stage: 'cancelled', status: 'cancelled', summary: 'Investigation cancelled. Saved work is preserved.' }])
        throw new Error('Investigation cancelled. Saved work is preserved.')
      }
      throw error
    } finally {
      if (investigation.current === controller) investigation.current = null
    }
  }

  function navigate(name: string, id: string) {
    setSection(name)
    void id
  }


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
      await displayRequest(await investigate<AskResponse>('/ask', { summary: ticket.trim() }))
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
          await investigate<AskResponse>(
            `/requests/${active.case_id}/resume`,
            { answer: answer.trim() },
          ),
        )
      } else
        await display(
          await investigate<Case>(`/cases/${active.case_id}/resume`, { answer: answer.trim() }),
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
      await display(await investigate<Case>(`/cases/${active.case_id}/investigate`))
    } catch (e) {
      setError((e as Error).message)
      await refreshAfterFailure(active.case_id)
    } finally {
      setBusy(false)
    }
  }
  async function openCase(id: string) {
    setSection('Investigations')
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

  async function continueChat(event: FormEvent) {
    event.preventDefault()
    if (!active?.transient || !answer.trim()) return
    setBusy(true)
    setError('')
    try {
      await displayRequest(await investigate<AskResponse>(`/requests/${active.case_id}/message`, { summary: answer.trim() }))
      setAnswer('')
    } catch (caught) { setError((caught as Error).message) }
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
          <p>Your support workspace</p>
        </div>
        <ProviderSelector />
        <span className="runtime-label">{readiness?.runtime_mode==='isolated_demo' ? 'Isolated demo' : 'Your workspace'}</span>
      </header>
      <nav className="workspace-nav" aria-label="Workspace">
        {([['Chat', 'chat'], ['Investigations', 'investigations'], ['Knowledge', 'knowledge-workspace'], ['Voice', 'voice']] as const).map(([name, id]) => (
          <button key={name} className={section === name ? 'active' : ''} aria-current={section === name ? 'page' : undefined} onClick={() => navigate(name, id)}>{name}</button>
        ))}
      </nav>
      {readiness?.runtime_mode === 'isolated_demo' && <div className="demo-banner runtime-banner"><strong>ISOLATED DEMO</strong><p>Responses are simulated in this isolated test session.</p></div>}
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      <main className={`workspace-main ${section.toLowerCase()}`}>
        <section hidden={section !== 'Chat'} className="chat-workspace" id="chat" aria-label="Chat workspace">
          <div className="workspace-heading"><div><span className="eyebrow">YOUR AI SERVICE DESK</span><h2>{active ? 'Let’s keep going' : 'How can I help?'}</h2></div>
            <button type="button" className="button button-neutral" disabled={busy} onClick={() => { setActive(null); setEvents([]); setTicket(''); setAnswer(''); setError('') }}>New conversation</button>
          </div>
          {!active && <div className="chat-welcome"><span className="welcome-mark" aria-hidden="true">D</span><h3>A little help, a clearer day.</h3><p>Work through an IT issue, ask a question, or find an answer in your documents.</p><div className="suggestion-row">{['My VPN is not connecting', 'Help with multi-factor sign-in', 'Find an answer in my documents'].map(suggestion => <button type="button" key={suggestion} onClick={() => setTicket(suggestion)}>{suggestion}</button>)}</div></div>}
          <form onSubmit={start} className="main-composer">
            <label htmlFor="ticket">Question or ticket</label>
            <textarea id="ticket" rows={3} placeholder="Message DeskMate…" value={ticket} onChange={e => setTicket(e.target.value)} required />
            <div className="composer-actions"><button type="button" className="button button-neutral" onClick={() => navigate('Voice', 'voice')} aria-label="Open voice support"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg></button>
              <div><button type="button" className="button button-subtle" disabled={busy || !ticket.trim()} onClick={() => void saveRequest()}>Save request</button><button className="button button-primary" disabled={busy || !ticket.trim() || (readiness !== null && !readiness.text_ready)} aria-busy={busy}>{busy ? 'Thinking…' : 'Send'}</button></div>
            </div>
            {blocker && <div className="availability-note"><p role="status">{blocker}</p><button type="button" className="button button-subtle" disabled={checking || busy} onClick={() => void refresh()}>Check availability</button></div>}
          </form>
        </section>
        <section hidden={section !== 'Investigations'} className="history-workspace" id="investigations" aria-label="Investigation history">
          <InvestigationHistory cases={cases} selected={active?.case_id} busy={busy} onOpen={openCase} allowSamples={readiness?.runtime_mode === 'isolated_demo'} />
        </section>
        <section hidden={section !== 'Knowledge'} id="knowledge-workspace" className="knowledge-workspace" aria-label="Knowledge workspace"><KnowledgePanel readiness={readiness} /></section>
        <section hidden={section !== 'Voice'} id="voice" className="voice-workspace"><VoiceSupport onCaseCreated={openCase} readiness={readiness} /></section>
        <div hidden={!['Chat', 'Investigations'].includes(section)}><InvestigationActivity events={activity} busy={busy && investigation.current !== null} onCancel={() => investigation.current?.abort()} /></div>
        <div className="investigation-panel" hidden={!active || !['Chat', 'Investigations'].includes(section)}>
          {!active ? (
            <section className="empty-state">
              <div aria-hidden="true">◎</div>
              <h2>Let’s work through it together.</h2>
              <p>
                Ask anything, troubleshoot an issue, or find answers in your documents.
                Your investigations and confirmed details stay in your workspace.
              </p>
            </section>
          ) : (
            <>
              <div className="case-heading">
                <div>
                  <span className="eyebrow">
                    {active.workflow === 'knowledge' ? 'KNOWLEDGE REQUEST' : 'CURRENT CASE'}
                  </span>
                  <p hidden className="case-id" data-testid="case-id">
                    {active.case_id}
                  </p>
                </div>
                <span className={`status-pill ${active.status}`} role="status">
                  {active.status.replace(/_/g, ' ')}
                </span>
              </div>
              <p className="ticket-summary">{active.ticket.summary}</p>
              {active.workflow === 'knowledge' && !active.chat_messages?.length && (
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
              {!!active.chat_messages?.length && <section className="card conversation-card" aria-label="Conversation">
                <h2 className="sr-only">Conversation</h2>
                <div className="chat-history" role="log" aria-live="polite">
                  {active.chat_messages.map((message, index) => <div key={index} className={`chat-message ${message.role}`}><strong>{message.role === 'user' ? 'You' : 'DeskMate'}</strong><p>{message.content}</p></div>)}
                </div>
                <form onSubmit={continueChat} className="chat-reply">
                  <label htmlFor="chat-reply">Reply to this conversation</label>
                  <textarea id="chat-reply" rows={2} value={answer} onChange={event => setAnswer(event.target.value)} required />
                  <button className="primary" disabled={busy || !answer.trim()}>{busy ? 'Thinking…' : 'Send message'}</button>
                </form>
              </section>}
              {!active.chat_messages?.length && <><details className="card investigation-activity"><summary>Investigation progress</summary>
                <h2>
                  {active.workflow === 'knowledge' ? 'Research summary' : 'Investigation Timeline'}
                </h2>
                <InvestigationTimeline events={active.workflow==='knowledge' ? events.filter(event=>/^(search_|fetch_|ingest_|evaluate_sources$|verify$)/.test(event.tool ?? '')) : events} />
                {busy && (
                  <p className="muted" role="status">
                    Gathering evidence…
                  </p>
                )}
              </details>
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
              <details className="card assessment" open={section === 'Investigations'}>
                <summary>Investigation details</summary>
                <h2 className="sr-only">
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
              </details>
              <details className="card agent-activity" hidden>
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
              </>}
            </>
          )}
        </div>
      </main>
      <footer>DeskMate · Thoughtful support, one conversation at a time.</footer>
    </div>
  )
}
