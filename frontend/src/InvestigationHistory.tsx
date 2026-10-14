import { useState } from 'react'
import type { Case } from './App'

export function sampleCase(item: Case): boolean {
  if (item.origin && item.origin !== 'unknown') return ['demo', 'test'].includes(item.origin)
  return /^(fictional\s|demo:|acceptance test:)/i.test(item.ticket.summary)
}

export function caseStatus(item: Case): 'active' | 'resolved' | 'failed' {
  if (item.resolution_status === 'user_confirmed_resolved') return 'resolved'
  return item.status === 'error' ? 'failed' : 'active'
}

export default function InvestigationHistory({ cases, selected, busy, onOpen }: {
  cases: Case[]; selected?: string; busy: boolean; onOpen: (id: string) => Promise<void>
}): JSX.Element {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState('active')
  const [origin, setOrigin] = useState('workspace')
  const [page, setPage] = useState(0)
  const filtered = cases.filter(item =>
    (filter === 'all' || caseStatus(item) === filter) &&
    (origin === 'all' || sampleCase(item) === (origin === 'samples')) &&
    item.ticket.summary.toLowerCase().includes(search.toLowerCase()),
  )
  const pages = Math.max(1, Math.ceil(filtered.length / 10))
  const current = Math.min(page, pages - 1)
  return <div className="history-card">
    <div className="workspace-heading"><div><span className="eyebrow">YOUR WORK, IN ONE PLACE</span><h2>Investigations</h2></div><span className="count">{filtered.length}</span></div>
    <div className="history-filters">
      <div><label htmlFor="case-search">Search investigations</label><input id="case-search" placeholder="Search by issue…" value={search} onChange={event => { setSearch(event.target.value); setPage(0) }} /></div>
      <div><label htmlFor="case-filter">Status</label><select id="case-filter" value={filter} onChange={event => { setFilter(event.target.value); setPage(0) }}><option value="active">Active</option><option value="resolved">Resolved</option><option value="failed">Failed</option><option value="all">All statuses</option></select></div>
      <div><label htmlFor="case-origin">History</label><select id="case-origin" value={origin} onChange={event => { setOrigin(event.target.value); setPage(0) }}><option value="workspace">Workspace</option><option value="samples">Demo and tests</option><option value="all">All entries</option></select></div>
    </div>
    {!filtered.length ? <div className="history-empty"><h3>No investigations here yet</h3><p>{cases.length ? 'Try a different search or history filter.' : 'Describe an issue in Chat to get started.'}</p></div> : <ul className="case-list">
      {filtered.slice(current * 10, current * 10 + 10).map(item => <li key={item.case_id}><button data-testid={`case-${item.case_id}`} disabled={busy} className={selected === item.case_id ? 'selected' : ''} onClick={() => void onOpen(item.case_id)}><span>{item.ticket.summary}</span><small className={`status-pill ${item.status}`}>{caseStatus(item) === 'resolved' ? 'Resolved' : item.status.replace(/_/g, ' ')}</small></button></li>)}
    </ul>}
    {pages > 1 && <div className="history-pagination"><button type="button" className="button button-neutral" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button><span>Page {current + 1} of {pages}</span><button type="button" className="button button-neutral" disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>Next</button></div>}
    <p className="history-note">Most recent first. Demo and test entries are kept in their own view.</p>
  </div>
}
