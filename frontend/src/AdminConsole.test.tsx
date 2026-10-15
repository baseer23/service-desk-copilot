import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import AdminConsole from './AdminConsole'

test('shows protected readiness without financial controls', async () => {
  const fetch = vi.fn(async () => Response.json({ text_ready: true }))
  vi.stubGlobal('fetch', fetch)
  render(<AdminConsole />)
  expect(await screen.findByText(/text_ready/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Disable Azure/ })).not.toBeInTheDocument()
  vi.unstubAllGlobals()
})
