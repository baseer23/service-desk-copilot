import type { ActivityEvent } from './investigationStream'

const GROUPS = [
  { title: 'Understanding the request', match: /understand|intent|classif|follow_up/ },
  { title: 'Planning the investigation', match: /plan|research|source_decision/ },
  { title: 'Checking available information', match: /tool:|search|fetch|source_selection|evidence_collected/ },
  { title: 'Evaluating what was found', match: /evaluat|assess|grading|reflection/ },
  { title: 'Verifying the answer', match: /verif|contradiction/ },
  { title: 'Preparing the response', match: /final|outcome|complete/ },
]

function groupFor(event: ActivityEvent): string {
  return GROUPS.find(group => group.match.test(`${event.stage} ${event.kind}`))?.title
    ?? 'Checking available information'
}

export default function InvestigationActivity({ events, busy, onCancel }: { events: ActivityEvent[]; busy: boolean; onCancel: () => void }): JSX.Element | null {
  if (!events.length && !busy) return null
  const latest = new Map<string, ActivityEvent>()
  for (const event of events) latest.set(groupFor(event), event)
  return <aside className="card working-panel" aria-label="How DeskMate is working">
    <div className="working-heading"><div><span className="eyebrow">LIVE INVESTIGATION</span><h2>How DeskMate is working</h2></div>{busy && <span className="working-pulse">Working</span>}</div>
    <ol className="working-stages" aria-live="polite">
      {GROUPS.map(group => {
        const event = latest.get(group.title)
        return <li key={group.title} className={event ? `stage-${event.status}` : ''}>
          <span className="stage-marker" aria-hidden="true">{event?.status === 'completed' ? '✓' : event ? '•' : '○'}</span>
          <div><strong>{group.title}</strong><p>{event?.summary ?? 'Waiting for this step.'}</p></div>
        </li>
      })}
    </ol>
    <details className="technical-activity"><summary>Technical activity</summary><ol>{events.map(event => <li key={`${event.sequence}-${event.event_id ?? ''}`}><strong>{event.stage.replace(/^tool:/, '').replace(/_/g, ' ')}</strong><span>{event.summary}</span><small>{event.status}{event.duration_ms !== undefined ? ` · ${(event.duration_ms / 1000).toFixed(1)}s` : ''}</small></li>)}</ol></details>
    {busy && <button type="button" className="button button-subtle" onClick={onCancel}>Cancel investigation</button>}
  </aside>
}
