import { providerHeaders } from './providerSelection'
export { providerHeaders } from './providerSelection'

const base = import.meta.env.VITE_API_BASE ?? ''

export function publicErrorMessage(message: string): string {
  if (/^(?:Groq (?:is not configured|is rate limited|capacity|request (?:failed|exceeds)|returned invalid structured))/.test(message)) return message
  if (/^Azure OpenAI (?:authentication failed|denied this identity|could not find the configured resource or deployment|is rate limited or out of quota|timed out|request was too large|returned an invalid structured response|could not complete the request)/.test(message)) return message
  if (/Azure|MCP|LangGraph|SQLite|Chroma|Cosmos|managed state|managed usage|Key.?Vault|rate_limit|[A-Z]+_[A-Z_]{3,}|provider|deployment|usage guard|pdftoppm|Tesseract/i.test(message))
    return 'DeskMate cannot complete this request right now. Your saved work is available. Please try again later or contact your administrator.'
  return message
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(base + path, { ...init, headers: providerHeaders(init?.headers) })
  } catch {
    throw new Error('DeskMate could not connect to the server. Please try again.')
  }
  let body: unknown
  try {
    body = await response.json()
  } catch {
    const contentType = response.headers.get('content-type')?.split(';', 1)[0] || 'unknown content type'
    const endpoint = path.split('?', 1)[0]
    throw new Error(
      response.ok
        ? `DeskMate received ${contentType} instead of JSON from ${endpoint}. Check that the backend and development proxy are running, then retry.`
        : response.status >= 500
          ? `The DeskMate server is temporarily unavailable (HTTP ${response.status}). Please try again.`
          : `Request failed (HTTP ${response.status}). The server returned an empty or invalid response.`,
    )
  }
  if (!response.ok) {
    const error = body as { detail?: unknown; case_id?: unknown; code?: unknown } | null
    const detail = error?.detail
    const structured =
      detail && typeof detail === 'object'
        ? (detail as { message?: unknown; code?: unknown })
        : null
    const validation = Array.isArray(detail)
      ? detail
          .map((item) => (typeof item?.msg === 'string' ? item.msg : 'Invalid input'))
          .join('; ')
      : null
    throw Object.assign(
      new Error(publicErrorMessage(
        typeof detail === 'string'
          ? detail
          : typeof structured?.message === 'string'
            ? structured.message
            : validation
              ? validation
              : `Request failed (${response.status})`,
      )),
      {
        status: response.status,
        code:
          typeof structured?.code === 'string'
            ? structured.code
            : typeof error?.code === 'string'
              ? error.code
              : undefined,
        case_id: typeof error?.case_id === 'string' ? error.case_id : undefined,
      },
    )
  }
  return body as T
}

export const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
})
