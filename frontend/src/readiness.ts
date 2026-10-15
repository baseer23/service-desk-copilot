import { useCallback, useEffect, useState } from 'react'
import { request } from './api'
import { providerChangedEvent } from './providerSelection'

export interface CapabilityReadiness {
  configured: boolean
  locally_initialized: boolean
  authenticated: string
  reachable: string
  live_validated: boolean
  missing: string[]
}
export interface ReadinessReport {
  runtime_mode: 'isolated_demo' | 'azure' | 'standard'
  managed_documents?: boolean
  reasoning: CapabilityReadiness
  embeddings: CapabilityReadiness
  voice: CapabilityReadiness
  local_mcp: string
  private_stores: string
  session_auth_configured: boolean
  text_ready: boolean
  voice_ready: boolean
  live_azure_validated: boolean
  local_documents_ready?: boolean
  local_ocr_available?: boolean
  semantic_ingestion_ready?: boolean
  semantic_ingestion_blocker?: string | null
  url_ingestion_enabled?: boolean
  azure_validation?: Record<string, { passed: boolean; code: string }>
  voice_validation_scope?: 'not_checked' | 'configuration' | 'transport'
  azure_usage?: {
    enabled: boolean
    remaining_estimated_cad: number
    estimated_used_cad: number
    limit_cad: number
    invoice_cap: false
    ledger_error: boolean
  }
}
export function reasoningBlocker(report: ReadinessReport | null): string | null {
  if (!report || report.text_ready) return null
  return 'Automated investigation is temporarily unavailable. You can still save your request, upload documents and search available knowledge.'
}

export function useReadiness(supplied?: ReadinessReport | null): {
  readiness: ReadinessReport | null
  refresh: () => Promise<void>
  checking: boolean
} {
  const [readiness, setReadiness] = useState<ReadinessReport | null>(supplied ?? null)
  const [checking, setChecking] = useState(false)
  const refresh = useCallback(async () => {
    setChecking(true)
    try {
      const report = await request<ReadinessReport>('/readiness')
      setReadiness(report.runtime_mode && typeof report.text_ready === 'boolean' && typeof report.voice_ready === 'boolean' ? report : null)
    } catch {
      setReadiness(null)
    } finally {
      setChecking(false)
    }
  }, [])
  useEffect(() => {
    if (supplied === undefined) void refresh()
    else setReadiness(supplied)
  }, [supplied, refresh])
  useEffect(() => {
    const update = () => void refresh()
    window.addEventListener('azure-usage-disabled', update)
    window.addEventListener(providerChangedEvent, update)
    return () => { window.removeEventListener('azure-usage-disabled', update); window.removeEventListener(providerChangedEvent, update) }
  }, [refresh])
  return { readiness, refresh, checking }
}
