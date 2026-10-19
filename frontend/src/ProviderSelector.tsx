import { useEffect, useState } from 'react'
import { getSelectedProvider, providerChangedEvent, providerStorageKey, type ReasoningProvider, setSelectedProvider } from './providerSelection'

export function ProviderSelector(): JSX.Element {
  const [selected, setSelected] = useState(getSelectedProvider)
  useEffect(() => {
    const refresh = () => setSelected(getSelectedProvider())
    const storage = (event: StorageEvent) => {
      if (event.key === providerStorageKey) refresh()
    }
    window.addEventListener(providerChangedEvent, refresh)
    window.addEventListener('storage', storage)
    return () => {
      window.removeEventListener(providerChangedEvent, refresh)
      window.removeEventListener('storage', storage)
    }
  }, [])
  return <label className="provider-selector" style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginLeft: 'auto', fontSize: '.85rem' }}>
    <span>Provider</span>
    <select aria-label="Reasoning provider" value={selected} onChange={event => setSelectedProvider(event.target.value as ReasoningProvider)} style={{ border: '1px solid #dbe2ea', borderRadius: '.55rem', padding: '.45rem .6rem', background: 'white', color: '#233249' }}>
      <option value="groq">Groq</option>
      <option value="azure_openai">Azure OpenAI</option>
    </select>
  </label>
}
