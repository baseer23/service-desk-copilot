import type { ActivityEvent } from './investigationStream'

export default function InvestigationActivity({ events, busy, onCancel }: { events: ActivityEvent[]; busy: boolean; onCancel: () => void }): JSX.Element | null {
  if (!events.length && !busy) return null
  return <details className="card investigation-activity" open={busy}>
    <summary>Investigation Activity</summary>
    <ol aria-label="Investigation activities" aria-live="polite">
      {events.map(event => <li key={event.sequence}>
        <span>{event.summary}</span>{' '}<small>{event.status}{event.duration_ms !== undefined ? ` · ${(event.duration_ms / 1000).toFixed(1)}s` : ''}</small>
      </li>)}
    </ol>
    {busy && <button type="button" className="button button-subtle" onClick={onCancel}>Cancel investigation</button>}
  </details>
}
