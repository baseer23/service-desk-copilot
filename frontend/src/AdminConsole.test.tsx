import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import AdminConsole from './AdminConsole'

test('failed emergency shutdown is visible and never reported as confirmed', async () => {
  const fetch = vi.fn(async (path: string) => path === '/admin/readiness'
    ? Response.json({ azure_usage: { disabled: true } })
    : Response.json({ detail: 'private storage diagnostics' }, { status: 503 }))
  vi.stubGlobal('fetch', fetch)
  render(<AdminConsole />)
  await userEvent.click(await screen.findByRole('button', { name: 'Disable Azure usage' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Shutdown could not be confirmed.')
  expect(screen.queryByText('private storage diagnostics')).not.toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/admin/azure/usage/disable', expect.objectContaining({ method: 'POST' }))
  vi.unstubAllGlobals()
})
