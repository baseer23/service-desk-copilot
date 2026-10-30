import { useState } from 'react'
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
  const [view, setView] = useState<'activity' | 'diagnostics'>('activity')
  if (!events.length && !busy) return null
  const latest = new Map<string, ActivityEvent>()
  const visibleEvents = events.filter(event => event.kind !== 'llm_usage' && event.kind !== 'context_compaction')
  const diagnosticEvents = events.filter(event => event.kind === 'llm_usage' || event.kind === 'context_compaction')
  for (const event of visibleEvents) latest.set(groupFor(event), event)
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
    <div className="activity-view-tabs" role="tablist" aria-label="Investigation detail view">
      <button type="button" role="tab" aria-selected={view === 'activity'} onClick={() => setView('activity')}>Investigation Activity</button>
      <button type="button" role="tab" aria-selected={view === 'diagnostics'} onClick={() => setView('diagnostics')}>Diagnostics</button>
    </div>
    <ol className="investigation-activity" hidden={view !== 'activity'}>
      {visibleEvents.map(event => <li key={`${event.sequence}-${event.event_id ?? ''}`}>
        <strong>{event.stage.replace(/^tool:/, '').replace(/_/g, ' ').replace(/^./, value => value.toUpperCase())} · {event.status}</strong>
        {event.query && <p className="activity-detail">{event.query}</p>}
        {event.objective && <p className="activity-detail"><span>Objective: </span><span>{event.objective}</span></p>}
        {event.rationale && <p className="activity-detail"><span>Why: </span><span>{event.rationale}</span></p>}
        {event.research_questions?.map(value => <p className="activity-detail" key={value}><span>Question: </span><span>{value}</span></p>)}
        {event.actions?.map(value => <p className="activity-detail" key={`${value.tool}-${value.reason}`}><span>{value.tool}: </span><span>{value.reason}</span></p>)}
        {event.findings?.map(value => <p className="activity-detail" key={value}><span>Found: </span><span>{value}</span></p>)}
        {event.missing_information?.map(value => <p className="activity-detail" key={value}><span>Missing: </span><span>{value}</span></p>)}
        {event.skipped_sources?.map(value => <p className="activity-detail" key={`${value.source}-${value.reason}`}><span>Skipped {value.source}: </span><span>{value.reason}</span></p>)}
        {event.next_action && <p className="activity-detail"><span>Next: </span><span>{event.next_action}</span></p>}
        {!!event.sources?.length && <ul className="activity-sources">{event.sources.map(source => <li key={`${source.url ?? source.title}-${source.page ?? ''}`}>
          {source.url ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> : source.title}
          {(source.page || source.extraction_method) && <small>{[
            source.page ? `Page ${source.page}` : '',
            source.extraction_method === 'document_intelligence' ? 'Azure Document Intelligence layout' : source.extraction_method ?? '',
          ].filter(Boolean).join(' · ')}</small>}
        </li>)}</ul>}
      </li>)}
    </ol>
    {view === 'diagnostics'
      ? <ol className="technical-activity">{events.map(event => <li key={`${event.sequence}-${event.event_id ?? ''}`}><strong>{event.stage.replace(/^tool:/, '').replace(/_/g, ' ')}</strong><span>{event.summary}</span><small>{event.status}{event.duration_ms !== undefined ? ` · ${(event.duration_ms / 1000).toFixed(1)}s` : ''}</small></li>)}</ol>
      : <div hidden>{diagnosticEvents.map(event => <span key={`${event.sequence}-${event.event_id ?? ''}`}>{event.summary}</span>)}</div>}
    {busy && <button type="button" className="button button-subtle" onClick={onCancel}>Cancel investigation</button>}
  </aside>
}
