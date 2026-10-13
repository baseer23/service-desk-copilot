import { afterEach, expect, test, vi } from 'vitest'
import { request } from './api'

afterEach(() => vi.unstubAllGlobals())

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
    message: 'Azure disabled; save a request locally.',
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
