import { StrictMode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import AuthGate from './AuthGate'

const identity = vi.hoisted(() => ({ initialize: vi.fn(async () => undefined), handleRedirectPromise: vi.fn(async () => ({ accessToken: 'explicit-unit-test-token' })), loginRedirect: vi.fn() }))
vi.mock('@azure/msal-browser', () => ({ BrowserCacheLocation: { MemoryStorage: 'memoryStorage' }, PublicClientApplication: class { initialize = identity.initialize; handleRedirectPromise = identity.handleRedirectPromise; loginRedirect = identity.loginRedirect } }))

test('StrictMode exchanges sign-in once and keeps administration hidden for normal users', async () => {
  const fetch = vi.fn(async (path: string) => Response.json(path === '/auth/configuration' ? { required: true, client_id: 'fictional-client', authority: 'https://login.microsoftonline.com/fictional-tenant', scope: 'api://fictional-client/DeskMate.Access' } : { authenticated: true, operator: false }))
  vi.stubGlobal('fetch', fetch)
  render(<StrictMode><AuthGate><p>Your investigations</p></AuthGate></StrictMode>)
  expect(await screen.findByText('Your investigations')).toBeInTheDocument()
  expect(identity.initialize).toHaveBeenCalledOnce()
  expect(identity.handleRedirectPromise).toHaveBeenCalledOnce()
  expect(fetch.mock.calls.filter(([path]) => path === '/auth/session')).toHaveLength(1)
  expect(screen.queryByRole('link', { name: 'Administration' })).not.toBeInTheDocument()
  expect(screen.queryByText(/fictional-client|fictional-tenant|MCP|Azure/)).not.toBeInTheDocument()
  fetch.mockImplementation(async () => Response.json({ error: 'private provider detail' }, { status: 503 }))
  await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Sign-out could not complete. Please try again.')
  expect(screen.getByRole('button', { name: 'Sign out' })).toBeEnabled()
  expect(screen.queryByText('private provider detail')).not.toBeInTheDocument()
  vi.unstubAllGlobals()
})
