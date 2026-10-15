import { post, providerHeaders, publicErrorMessage } from './api'

export interface ActivityEvent {
  sequence: number
  kind: string
  stage: string
  status: 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled'
  summary: string
  duration_ms?: number
  wait_seconds?: number
  result_count?: number
  event_id?: string
  query?: string
  rationale?: string
  objective?: string
  research_queries?: string[]
  research_questions?: string[]
  findings?: string[]
  missing_information?: string[]
  next_action?: string
  actions?: Array<{ tool: string; reason: string }>
  sources?: Array<{ title: string; url?: string | null }>
  category?: string
  request_id?: string | null
}

export function mergeActivity(current: ActivityEvent[], event: ActivityEvent): ActivityEvent[] {
  if (current.some(item => item.sequence === event.sequence)) return current
  if (event.event_id && current.some(item => item.event_id === event.event_id)) return current
  return [...current, event]
}

export async function streamInvestigation<T>(path: string, body: unknown, onActivity: (event: ActivityEvent) => void, signal?: AbortSignal): Promise<T> {
  let response: Response
  try { response = await fetch((import.meta.env.VITE_API_BASE ?? '') + path, {
    ...post(body), headers: providerHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }), signal,
  }) } catch (error) {
    if (signal?.aborted) throw error
    throw new Error('DeskMate could not connect to the server. Please try again.')
  }
  // JSON is supported for older servers and explicit test transports; never retry a POST.
  if (!response.headers?.get('content-type')?.includes('text/event-stream')) {
    let value
    try { value = await response.json() } catch {
      throw new Error(response.ok ? 'DeskMate received an empty or invalid server response. Reload the page and try again.' : response.status >= 500 ? `The DeskMate server is temporarily unavailable (HTTP ${response.status}). Please try again.` : `Request failed (HTTP ${response.status}). The server returned an empty or invalid response.`)
    }
    if (!response.ok) throw Object.assign(new Error(publicErrorMessage(typeof value.detail === 'string' ? value.detail : value.detail?.message || 'The investigation could not finish.')), { case_id: value.case_id })
    return value as T
  }
  if (!response.ok || !response.body) throw new Error('The investigation could not start. Please try again.')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = '', result: T | undefined
  try {
    let finished = false
    while (!finished) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let boundary: number
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2)
        const name = block.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim()
        const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const parsed = JSON.parse(data)
        if (name === 'activity') onActivity(parsed)
        if (name === 'result') result = parsed as T
        if (name === 'error') throw Object.assign(new Error(publicErrorMessage(typeof parsed.detail === 'string' ? parsed.detail : parsed.detail?.message || 'The investigation could not finish.')), { case_id: parsed.case_id })
      }
      finished = done
    }
  } finally { reader.releaseLock() }
  if (result === undefined) throw new Error('The investigation connection ended before a response was received. Your saved work is preserved.')
  return result
}
