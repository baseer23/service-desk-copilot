import type { ActivityEvent } from './investigationStream'

export default function InvestigationActivity({ events, busy, onCancel }: { events: ActivityEvent[]; busy: boolean; onCancel: () => void }): JSX.Element | null {
  if (!events.length && !busy) return null
  return <details className="card investigation-activity" open={busy}>
    <summary>Investigation Activity</summary>
    <ol aria-label="Investigation activities" aria-live="polite">
      {events.map(event => <li key={event.sequence}>
        <div><span>{event.summary}</span>{' '}<small>{event.status}{event.duration_ms !== undefined ? ` · ${(event.duration_ms / 1000).toFixed(1)}s` : ''}</small></div>
        {event.query && <p className="activity-detail"><strong>Query:</strong> {event.query}</p>}
        {event.rationale && <p className="activity-detail">{event.rationale}</p>}
        {event.actions?.length ? <ul className="activity-detail">{event.actions.map((action, index) => <li key={`${action.tool}-${index}`}><strong>{action.tool.replace(/_/g, ' ')}</strong>{action.reason ? ` — ${action.reason}` : ''}</li>)}</ul> : null}
        {event.research_queries?.length ? <ul className="activity-detail">{event.research_queries.map(query => <li key={query}>{query}</li>)}</ul> : null}
        {event.sources?.length ? <ul className="activity-sources">{event.sources.map((source, index) => <li key={`${source.url ?? source.title}-${index}`}>{source.url && /^https:\/\//i.test(source.url) ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title}</li>)}</ul> : null}
      </li>)}
    </ol>
    {busy && <button type="button" className="button button-subtle" onClick={onCancel}>Cancel investigation</button>}
  </details>
}
