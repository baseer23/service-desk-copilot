import { afterEach, expect, test, vi } from 'vitest'
import { publicErrorMessage, request } from './api'

afterEach(() => vi.unstubAllGlobals())

test.each(['Managed state could not be read', 'SQLite storage is unavailable', 'Cosmos service timeout'])('keeps infrastructure errors out of the normal interface: %s', message => {
  expect(publicErrorMessage(message)).toBe('DeskMate cannot complete this request right now. Your saved work is available. Please try again later or contact your administrator.')
})

test('shows input validation errors instead of a generic HTTP code', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ detail: [{ msg: 'Text is required' }] }, { status: 422 })),
  )
  await expect(request('/documents/paste')).rejects.toThrow('Text is required')
})
