import type { ActivityEvent } from './investigationStream'

function ActivityRow({ event }: { event: ActivityEvent }): JSX.Element {
  return <li>
    <div><span>{event.summary}</span>{' '}<small>{event.status}{event.duration_ms !== undefined ? ` · ${(event.duration_ms / 1000).toFixed(1)}s` : ''}</small></div>
    {event.objective && <p className="activity-detail"><strong>Objective:</strong> {event.objective}</p>}
    {event.query && <p className="activity-detail"><strong>Query:</strong> {event.query}</p>}
    {event.question && <p className="activity-detail"><strong>Question:</strong> {event.question}</p>}
    {event.rationale && <p className="activity-detail">{event.rationale}</p>}
    {event.research_questions?.length ? <div className="activity-detail"><strong>Research questions</strong><ul>{event.research_questions.map(question => <li key={question}>{question}</li>)}</ul></div> : null}
    {event.actions?.length ? <ul className="activity-detail">{event.actions.map((action, index) => <li key={`${action.tool}-${index}`}><strong>{action.tool.replace(/_/g, ' ')}</strong>{action.reason ? ` — ${action.reason}` : ''}</li>)}</ul> : null}
    {event.research_queries?.length ? <ul className="activity-detail">{event.research_queries.map(query => <li key={query}>{query}</li>)}</ul> : null}
    {event.findings?.length ? <div className="activity-detail"><strong>Observed</strong><ul>{event.findings.map(finding => <li key={finding}>{finding}</li>)}</ul></div> : null}
    {event.missing_information?.length ? <div className="activity-detail"><strong>Still needed</strong><ul>{event.missing_information.map(item => <li key={item}>{item}</li>)}</ul></div> : null}
    {event.next_action && <p className="activity-detail"><strong>Next:</strong> {event.next_action}</p>}
    {event.conclusion && <p className="activity-detail"><strong>Conclusion:</strong> {event.conclusion}</p>}
    {event.sources?.length ? <ul className="activity-sources">{event.sources.map((source, index) => {
      const linkable = !!source.url && (/^https:\/\//i.test(source.url) || /^\/documents\/[a-f0-9]{64}\/download$/.test(source.url))
      const method = source.extraction_method === 'document_intelligence' ? 'Azure Document Intelligence layout' : source.extraction_method ? source.extraction_method.replace(/_/g, ' ') : null
      return <li key={`${source.url ?? source.title}-${index}`}>
        {linkable ? <a href={source.url!} target={source.url!.startsWith('http') ? '_blank' : undefined} rel={source.url!.startsWith('http') ? 'noreferrer' : undefined}>{source.title}</a> : source.title}
        {(source.page || method) && <small> · {[source.page ? `Page ${source.page}` : null, method].filter(Boolean).join(' · ')}</small>}
      </li>
    })}</ul> : null}
  </li>
}

export default function InvestigationActivity({ events, busy, onCancel }: { events: ActivityEvent[]; busy: boolean; onCancel: () => void }): JSX.Element | null {
  if (!events.length && !busy) return null
  const diagnostics = events.filter(event => event.kind.startsWith('llm_') || event.kind === 'context_compaction' || event.kind === 'provider_fallback')
  const investigation = events.filter(event => !diagnostics.includes(event))
  return <details className="card investigation-activity" open={busy}>
    <summary>Investigation Activity</summary>
    <ol aria-label="Investigation activities" aria-live="polite">
      {investigation.map(event => <ActivityRow key={event.sequence} event={event} />)}
    </ol>
    {!!diagnostics.length && <details><summary>Diagnostics</summary><ol>{diagnostics.map(event => <ActivityRow key={event.sequence} event={event} />)}</ol></details>}
    {busy && <button type="button" className="button button-subtle" onClick={onCancel}>Cancel investigation</button>}
  </details>
}
