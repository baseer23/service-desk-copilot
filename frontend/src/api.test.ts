import { afterEach, expect, test, vi } from 'vitest'
import { publicErrorMessage, request } from './api'

afterEach(() => vi.unstubAllGlobals())

test.each(['Managed state could not be read', 'Managed usage ledger unavailable', 'Cosmos service timeout'])('keeps infrastructure errors out of the normal interface: %s', message => {
  expect(publicErrorMessage(message)).toBe('DeskMate cannot complete this request right now. Your saved work is available. Please try again later or contact your administrator.')
})

test('shows the structured readiness explanation and safe code', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Response.json(
        {
          detail: {
            code: 'AZURE_REASONING_NOT_READY',
            message: 'Azure disabled; save a request locally.',
          },
        },
        { status: 503 },
      ),
    ),
  )
  await expect(request('/ask')).rejects.toMatchObject({
    message: 'DeskMate cannot complete this request right now. Your saved work is available. Please try again later or contact your administrator.',
    code: 'AZURE_REASONING_NOT_READY',
    status: 503,
  })
})

test('shows input validation errors instead of a generic HTTP code', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ detail: [{ msg: 'Text is required' }] }, { status: 422 })),
  )
  await expect(request('/documents/paste')).rejects.toThrow('Text is required')
})
