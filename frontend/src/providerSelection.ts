export type ReasoningProvider = 'groq' | 'azure_openai' | 'azure_gpt5_nano'

export const providerStorageKey = 'deskmate.reasoning-provider'
export const providerChangedEvent = 'deskmate-provider-changed'
let memorySelection: ReasoningProvider = 'groq'

export function getSelectedProvider(): ReasoningProvider {
  try {
    const saved = localStorage.getItem(providerStorageKey)
    if (saved === 'groq' || saved === 'azure_openai' || saved === 'azure_gpt5_nano') return saved
    return 'groq'
  } catch {
    return memorySelection
  }
}

export function setSelectedProvider(provider: ReasoningProvider): void {
  memorySelection = provider
  try {
    localStorage.setItem(providerStorageKey, provider)
  } catch {
    // Private browsing may deny storage; selection still works for this page.
  }
  window.dispatchEvent(new Event(providerChangedEvent))
}

export function providerHeaders(existing?: HeadersInit): Headers {
  const headers = new Headers(existing)
  headers.set('X-DeskMate-Provider', getSelectedProvider())
  return headers
}
