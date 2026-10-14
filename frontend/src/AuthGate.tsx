import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { BrowserCacheLocation, PublicClientApplication } from '@azure/msal-browser'
import AdminConsole from './AdminConsole'

interface Configuration { required: boolean; client_id: string; authority: string; scope: string }

interface IdentitySession { config: Configuration; client: PublicClientApplication | null; ready: boolean; operator: boolean }
let initialization: Promise<IdentitySession> | null = null

async function prepareIdentity(): Promise<IdentitySession> {
  const response = await fetch('/auth/configuration')
  if (!response.ok) throw new Error('Configuration unavailable')
  const config: Configuration = await response.json()
  if (!config.required) return { config, client: null, ready: true, operator: false }
  const client = new PublicClientApplication({
    auth: { clientId: config.client_id, authority: config.authority, redirectUri: window.location.origin + '/' },
    cache: { cacheLocation: BrowserCacheLocation.MemoryStorage },
  })
  await client.initialize()
  const result = await client.handleRedirectPromise()
  if (result?.accessToken) {
    const session = await fetch('/auth/session', { method: 'POST', headers: { Authorization: 'Bearer ' + result.accessToken } })
    if (!session.ok) throw new Error('Sign-in unavailable')
  }
  const session = await fetch('/auth/me')
  const current = session.ok ? await session.json() : null
  return { config, client, ready: session.ok, operator: current?.operator === true }
}

export default function AuthGate({ children }: { children: ReactNode }): JSX.Element {
  const [session, setSession] = useState<IdentitySession | null>(null)
  const [error, setError] = useState('')
  const [signingOut, setSigningOut] = useState(false)
  const config = session?.config
  const client = session?.client
  const ready = session?.ready
  const operator = session?.operator
  useEffect(() => {
    let cancelled = false
    // One redirect exchange across StrictMode mounts and concurrent components.
    initialization ??= prepareIdentity()
    void initialization.then(current => { if (!cancelled) setSession(current) })
      .catch(() => { if (!cancelled) setError('Sign-in could not be prepared. Reload the page or contact your administrator.') })
    return () => { cancelled = true }
  }, [])

  if (ready) return <>
    {error && <p role="alert">{error}</p>}
    {config?.required && <div className="account-controls">
      {operator && <a href="/admin">Administration</a>}
      <button type="button" disabled={signingOut} onClick={() => {
        setSigningOut(true)
        void fetch('/auth/logout', { method: 'POST' }).then(response => {
          if (!response.ok) throw new Error('Sign-out unavailable')
          window.location.assign('/')
        }).catch(() => {
          setError('Sign-out could not complete. Please try again.')
          setSigningOut(false)
        })
      }}>Sign out</button>
    </div>}
    {window.location.pathname === '/admin' ? <AdminConsole /> : children}
  </>
  return <main className="sign-in card">
    <h1>DeskMate</h1><p>Investigate service desk issues and find reliable guidance.</p>
    {error ? <p role="alert">{error}</p> : !config ? <p role="status">Preparing your workspace…</p>
      : <><p>Sign in with your work account to open your investigations and documents.</p>
        <button type="button" disabled={!client} onClick={() => {
          void client?.loginRedirect({ scopes: [config?.scope ?? ''] }).catch(() => setError('Sign-in could not start. Please try again.'))
        }}>Sign in with Microsoft</button></>}
  </main>
}
