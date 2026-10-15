import { useEffect, useState } from 'react'
import { request } from './api'

/** Infrastructure details are available only after the protected operator API grants access. */
export default function AdminConsole(): JSX.Element {
  const [report, setReport] = useState<unknown>(null)
  const [error, setError] = useState('')
  const refresh = () => request('/admin/readiness').then(setReport).catch(() => setError('Administrator access is required or the service is unavailable.'))
  useEffect(() => { void refresh() }, [])
  return <main className="card"><h1>DeskMate administration</h1><a href="/">Back to investigations</a>
    {error && <p role="alert">{error}</p>}
    {report !== null && <><pre>{JSON.stringify(report, null, 2)}</pre>
      <button type="button" onClick={() => void refresh()}>Refresh diagnostics</button></>}
  </main>
}
