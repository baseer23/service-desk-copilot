import { afterEach, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ProviderSelector } from './ProviderSelector'
import { request } from './api'
import { getSelectedProvider, providerHeaders, providerStorageKey } from './providerSelection'

afterEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

test('defaults to Groq and persists an explicit selection across remounts', () => {
  const view = render(<ProviderSelector />)
  expect(screen.getByRole('option', { name: 'Groq' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'Azure OpenAI' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'Azure OpenAI — GPT-5 Nano (Low Cost)' })).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: 'Reasoning provider' })).toHaveValue('groq')
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'azure_openai' } })
  expect(localStorage.getItem(providerStorageKey)).toBe('azure_openai')
  view.unmount()
  render(<ProviderSelector />)
  expect(screen.getByRole('combobox')).toHaveValue('azure_openai')
})

test('GPT-5 Nano persists and is sent as its independent provider route', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => Response.json({ ok: true }))
  vi.stubGlobal('fetch', fetch)
  const view = render(<ProviderSelector />)
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'azure_gpt5_nano' } })
  expect(localStorage.getItem(providerStorageKey)).toBe('azure_gpt5_nano')
  view.unmount()
  render(<ProviderSelector />)
  expect(screen.getByRole('combobox')).toHaveValue('azure_gpt5_nano')
  await request('/ask', { method: 'POST', body: '{}' })
  expect(new Headers(fetch.mock.calls[0][1]?.headers).get('X-DeskMate-Provider')).toBe('azure_gpt5_nano')
})

test('selected provider is attached to subsequent requests without losing headers or body', async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => Response.json({ ok: true }))
  vi.stubGlobal('fetch', fetch)
  render(<ProviderSelector />)
  await request('/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  expect(new Headers(fetch.mock.calls[0][1]?.headers).get('X-DeskMate-Provider')).toBe('groq')
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'azure_openai' } })
  await request('/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
  const options = fetch.mock.calls[1][1]
  expect(new Headers(options?.headers).get('X-DeskMate-Provider')).toBe('azure_openai')
  expect(new Headers(options?.headers).get('Content-Type')).toBe('application/json')
  expect(options?.body).toBe('{}')
})

test('SSE requests use the same selected-provider headers and reject invalid persisted values', () => {
  localStorage.setItem(providerStorageKey, 'untrusted-provider')
  expect(getSelectedProvider()).toBe('groq')
  expect(providerHeaders({ Accept: 'text/event-stream' }).get('Accept')).toBe('text/event-stream')
  expect(providerHeaders().get('X-DeskMate-Provider')).toBe('groq')
})
