const base = import.meta.env.VITE_API_BASE ?? ''

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(base + path, init)
  } catch {
    throw new Error('DeskMate could not connect to the server. Please try again.')
  }
  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new Error(
      response.ok
        ? 'DeskMate received an empty or invalid server response. Reload the page and try again.'
        : response.status >= 500
          ? `The DeskMate server is temporarily unavailable (HTTP ${response.status}). Please try again.`
          : `Request failed (HTTP ${response.status}). The server returned an empty or invalid response.`,
    )
  }
  if (!response.ok) {
    const error = body as { detail?: unknown; case_id?: unknown; code?: unknown } | null
    throw Object.assign(
      new Error(
        typeof error?.detail === 'string' ? error.detail : `Request failed (${response.status})`,
      ),
      {
        status: response.status,
        code: typeof error?.code === 'string' ? error.code : undefined,
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
