/* eslint-disable jsx-a11y/no-noninteractive-tabindex -- Bounded scroll regions need keyboard focus to expose all evidence and events. */
import { useState } from 'react'

export interface ActivityEvent {
  id: number
  tool: string | null
  source: string
  content: string
  created_at: string
}
export interface EvidenceRecord {
  tool: string
  source: string
  source_system: string
  observed_at: string
  source_type?: string
  source_quality?: string
  url?: string | null
  content: unknown
}
export const providerLabel = (value: string): string =>
  value === 'stub' ? 'Isolated demo' : value === 'azure_openai' ? 'Azure reasoning' : value
export const timestamp = (value: string): string =>
  new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z').toLocaleString()
const safeActivity = (event: ActivityEvent): string => {
  if (event.tool === 'analyze_ticket') return 'Ticket analyzed'
  if (event.tool === 'verify') return 'Verifying evidence'
  if (event.tool === 'search_previous_tickets') return 'Checking previous incidents'
  if (event.tool === 'search_knowledge') return 'Searching knowledge'
  if (event.tool?.startsWith('search_')) return 'Searching evidence'
  if (event.tool?.startsWith('get_')) return 'Checking context'
  if (event.tool?.startsWith('ingest_')) return 'Saving knowledge'
  if (event.tool === 'human_input') return 'User confirmation received'
  if (event.tool === 'error') return 'Evidence source unavailable'
  return 'Preparing response'
}
export function InvestigationTimeline({ events }: { events: ActivityEvent[] }): JSX.Element {
  return (
    <div
      className="investigation-scroll"
      tabIndex={0}
      role="region"
      aria-label="Investigation timeline"
      data-testid="investigation-scroll"
    >
      <ol className="timeline">
        {events.map((event) => (
          <li key={event.id}>
            <span className="event-dot" aria-hidden="true">
              {event.tool === 'error' ? '!' : '✓'}
            </span>
            <div>
              <p>{safeActivity(event)}</p>
              <small>
                {providerLabel(event.source)} · {timestamp(event.created_at)}
              </small>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
function readable(content: unknown): string {
  if (typeof content === 'string') return content
  if (content && typeof content === 'object') {
    const row = content as Record<string, unknown>
    if (typeof row.text === 'string') return row.text
  }
  return JSON.stringify(content, null, 2) ?? ''
}
function EvidenceCard({ evidence }: { evidence: EvidenceRecord }): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const text = readable(evidence.content)
  const sourceClass =
    evidence.source_system === 'local_fixture'
      ? 'Fictional fixture'
      : evidence.source_quality === 'third_party'
        ? 'Third-party source'
        : evidence.source_quality === 'primary'
          ? 'Primary source'
          : 'Retrieved source'
  const row =
    evidence.content && typeof evidence.content === 'object'
      ? (evidence.content as Record<string, unknown>)
      : null
  const meta =
    row?.metadata && typeof row.metadata === 'object'
      ? (row.metadata as Record<string, unknown>)
      : null
  const title =
    typeof meta?.title === 'string'
      ? meta.title
      : typeof row?.title === 'string'
        ? row.title
        : evidence.source
  return (
    <article className="evidence-card">
      <header>
        <strong>{title}</strong>
        <span
          className={`source-badge ${evidence.source_system === 'local_fixture' ? 'fixture' : ''}`}
        >
          {sourceClass} · {providerLabel(evidence.source_system)}
        </span>
      </header>
      <small>
        {timestamp(evidence.observed_at)} · {evidence.tool.replace(/_/g, ' ')}
        {evidence.source_type ? ' · ' + evidence.source_type.replace(/_/g, ' ') : ''}
      </small>
      {evidence.url && /^https?:\/\//i.test(evidence.url) && (
        <p className="source-link">
          <a href={evidence.url} target="_blank" rel="noreferrer">
            {evidence.url}
          </a>
        </p>
      )}
      <p className="source-preview">
        {text.slice(0, 600)}
        {text.length > 600 ? '…' : ''}
      </p>
      <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>Show full evidence</summary>
        {expanded && (
          <pre tabIndex={0} aria-label="Full evidence">
            {typeof evidence.content === 'string'
              ? evidence.content
              : JSON.stringify(evidence.content, null, 2)}
          </pre>
        )}
      </details>
    </article>
  )
}
export function EvidenceList({ evidence }: { evidence: EvidenceRecord[] }): JSX.Element {
  return (
    <div
      className="evidence-list"
      tabIndex={0}
      role="region"
      aria-label="Sources and evidence"
      data-testid="evidence-scroll"
    >
      {evidence.map((item, index) => (
        <EvidenceCard key={index} evidence={item} />
      ))}
    </div>
  )
}
