import React, { useEffect, useMemo, useRef, useState } from 'react'

import { featureFlags } from '../featureFlags'

/**
 * Citation metadata rendered beneath assistant answers.
 */
export type Citation = {
  doc_id: string
  chunk_id: string
  score: number
  title?: string | null
  snippet?: string | null
}

export type Message = {
  id: string
  role: 'user' | 'assistant'
  text: string
  pending?: boolean
  error?: boolean
  citations?: Citation[]
  metadata?: Record<string, unknown>
}

type Props = {
  message: Message
}

const ROLE_LABEL: Record<Message['role'], string> = {
  assistant: 'DeskMate',
  user: 'You',
}

const SMART_OPEN = '\u201C'
const SMART_CLOSE = '\u201D'
const SHORT_QUOTE_LENGTH = 120
const LONG_QUOTE_LENGTH = 240

const normaliseWhitespace = (input: string) => input.replace(/\s+/g, ' ').trim()

const buildExcerpt = (raw: string, max: number) => {
  const text = normaliseWhitespace(raw)
  if (text.length <= max) return text
  return `${text.slice(0, max).trimEnd()}…`
}

const toSmartQuote = (text: string) => `${SMART_OPEN}${text}${SMART_CLOSE}`

const stripHexArtifacts = (input: string) =>
  input
    .replace(/(?:\b[0-9a-f]{16,}\b:?)+/gi, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()

const polishNarrative = (input: string) =>
  input
    .replace(/\(\s*\)/g, '')
    .replace(/:\s*-\s+/g, ': ')
    .replace(/([.!?])\s*-\s+/g, '$1 ')
    .replace(/\s+-\s+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .replace(/\s+([,.;:])/g, '$1')
    .replace(/([,.;:])(\S)/g, '$1 $2')
    .trim()

const sanitizeAssistantText = (text: string) => {
  const sentences = text.split(/(?<=[.!?])\s+/)
  const filteredSentences = sentences.filter((sentence) => {
    const normalised = sentence.trim()
    if (!normalised) return false
    const lower = normalised.toLowerCase()
    if (lower.includes('this information can be found at')) return false
    if (lower.includes('doc_id')) return false
    if (lower.includes('chunk_id')) return false
    return true
  })

  const joined = filteredSentences.join(' ').trim()
  const withoutInlineIds = stripHexArtifacts(
    joined
      .replace(/\[doc_id:[^\]]*\]/gi, '')
      .replace(/\[chunk_id:[^\]]*\]/gi, '')
      .replace(/\[[^:\]]+:[^\]]*\]/g, '')
      .trim(),
  )

  const polished = polishNarrative(withoutInlineIds)
  if (polished.length > 0) return polished

  const fallback = polishNarrative(stripHexArtifacts(
    text
      .replace(/\[doc_id:[^\]]*\]/gi, '')
      .replace(/\[chunk_id:[^\]]*\]/gi, '')
      .replace(/\[[^:\]]+:[^\]]*\]/g, '')
      .trim(),
  ))

  return fallback.length > 0 ? fallback : text
}

/**
 * Render a single chat bubble with optional citations and debug detail toggles.
 */
export default function MessageBubble({ message }: Props) {
  const label = ROLE_LABEL[message.role]
  const [showCitations, setShowCitations] = useState(false)
  const [copyKey, setCopyKey] = useState<string | null>(null)
  const [showDebug, setShowDebug] = useState(false)
  const copyTimeout = useRef<number | null>(null)
  const isPending = Boolean(message.pending)
  const avatar = message.role === 'user' ? 'U' : 'D'

  const displayText = useMemo(() => {
    if (message.role !== 'assistant') return message.text
    return sanitizeAssistantText(message.text)
  }, [message.role, message.text])

  useEffect(() => {
    return () => {
      if (copyTimeout.current) {
        window.clearTimeout(copyTimeout.current)
      }
    }
  }, [])

  const citations = useMemo(() => {
    if (message.role !== 'assistant' || !message.citations) return []

    return message.citations.map((citation, index) => {
      const snippet = citation.snippet?.trim()
      const base = snippet && snippet.length > 0 ? snippet : displayText
      const safeBase = base && base.length > 0 ? base : 'Preview unavailable.'
      const title = citation.title?.trim() && citation.title.trim().length > 0 ? citation.title.trim() : 'Untitled'
      const shortExcerpt = toSmartQuote(buildExcerpt(safeBase, SHORT_QUOTE_LENGTH))
      const longExcerpt = toSmartQuote(buildExcerpt(safeBase, LONG_QUOTE_LENGTH))
      const identifier = `${citation.doc_id}:${citation.chunk_id || index}`
      const relevance = Number.isFinite(citation.score) ? citation.score.toFixed(2) : undefined
      return {
        original: citation,
        title,
        shortExcerpt,
        longExcerpt,
        relevance,
        humanLine: `${longExcerpt} — ${title}`,
        id: identifier,
      }
    })
  }, [displayText, message.citations, message.role])

  const hasCitations = citations.length > 0

  const scheduleReset = () => {
    if (copyTimeout.current) {
      window.clearTimeout(copyTimeout.current)
    }
    copyTimeout.current = window.setTimeout(() => setCopyKey(null), 1600)
  }

  const copyText = async (content: string, key: string) => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content)
      } else if (typeof document !== 'undefined') {
        const textarea = document.createElement('textarea')
        textarea.value = content
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      setCopyKey(key)
      scheduleReset()
    } catch (error) {
      console.error('Failed to copy', error)
    }
  }

  const handleCopyAnswer = () => copyText(displayText, 'answer')

  const handleToggleCitations = () => setShowCitations((prev) => !prev)
  const handleToggleDebug = () => setShowDebug((prev) => !prev)

  const hasDebugDetails = useMemo(() => {
    const metadataEntries = message.metadata && Object.keys(message.metadata).length > 0
    const citationDebug = hasCitations
    return Boolean(metadataEntries || citationDebug)
  }, [hasCitations, message.metadata])

  return (
    <div className={`bubble ${message.role}${message.pending ? ' pending' : ''}${message.error ? ' error' : ''}`}>
      <div className="bubble-header">
        <span className="bubble-avatar" aria-hidden="true">
          {avatar}
        </span>
        <div className="bubble-body">
          <div className="bubble-role">{label}</div>
          {isPending ? (
            <>
              <div className="bubble-skeleton single" aria-hidden="true">
                <span className="skeleton-line" />
              </div>
              <span className="sr-only">{displayText}</span>
            </>
          ) : (
            <div className="bubble-text">{displayText}</div>
          )}
        </div>
        {message.role === 'assistant' && !isPending && (
          <div className="bubble-actions">
            <button
              type="button"
              className={`pill-button${
                featureFlags.twoLineCopyAnswerButton ? ' two-line' : ''
              }`}
              onClick={handleCopyAnswer}
              aria-label="Copy answer"
            >
              {featureFlags.twoLineCopyAnswerButton ? (
                <>
                  <span className="pill-button-line">Copy</span>
                  <span className="pill-button-line">Answer</span>
                </>
              ) : (
                copyKey === 'answer' ? 'Copied' : 'Copy answer'
              )}
            </button>
            {hasCitations && (
              <button
                type="button"
                className={`pill-button${
                  featureFlags.twoLineCitationsButton ? ' two-line' : ''
                }`}
                onClick={handleToggleCitations}
                aria-expanded={showCitations}
                aria-label="Copy citations"
              >
                {featureFlags.twoLineCitationsButton ? (
                  <>
                    <span className="pill-button-line">Copy</span>
                    <span className="pill-button-line">Citations</span>
                  </>
                ) : (
                  showCitations ? 'Hide citations' : 'Show citations'
                )}
              </button>
            )}
            {hasDebugDetails && (
              <button
                type="button"
                className={`pill-button${
                  featureFlags.twoLineDebugInfoButton ? ' two-line' : ''
                }`}
                onClick={handleToggleDebug}
                aria-expanded={showDebug}
                aria-label={showDebug ? 'Hide technical details' : 'Debug info'}
              >
                {featureFlags.twoLineDebugInfoButton ? (
                  <>
                    <span className="pill-button-line">Debug</span>
                    <span className="pill-button-line">Info</span>
                  </>
                ) : showDebug ? (
                  'Hide debug'
                ) : (
                  'Debug info'
                )}
              </button>
            )}
          </div>
        )}
      </div>

      {hasCitations && !isPending && (
        <div className="citation-summary">
          <ul className="citation-chips">
            {citations.map((citation) => (
              <li key={citation.id}>
                <button
                  type="button"
                  className="citation-chip"
                  onClick={() => copyText(citation.longExcerpt, `chip-${citation.id}`)}
                  aria-label={`Copy excerpt from ${citation.title}`}
                >
                  <span className="citation-chip-title">{citation.title}</span>
                  <span className="citation-chip-quote">{citation.shortExcerpt}</span>
                  <span className="citation-chip-copy">{copyKey === `chip-${citation.id}` ? 'Copied' : 'Copy snippet'}</span>
                </button>
              </li>
            ))}
          </ul>

          {showCitations && (
            <div className="citation-drawer">
              {citations.map((citation) => (
                <article key={`drawer-${citation.id}`} className="citation-entry">
                  <h4 className="citation-entry-title">{citation.title}</h4>
                  <p className="citation-entry-subtitle">{citation.longExcerpt}</p>
                  <div className="citation-entry-actions">
                    <button
                      type="button"
                      onClick={() => copyText(citation.humanLine, `quote-${citation.id}`)}
                      aria-label={`Copy quote from ${citation.title}`}
                    >
                      {copyKey === `quote-${citation.id}` ? 'Copied' : 'Copy quote'}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      )}

      {showDebug && hasDebugDetails && (
        <div className="debug-panel">
          {citations.length > 0 && (
            <div className="debug-block">
              <h4>Sources</h4>
              <ul>
                {citations.map((citation) => (
                  <li key={`debug-source-${citation.id}`}>
                    <strong>{citation.title}</strong>
                    <div className="debug-meta">
                      <span>doc_id: {citation.original.doc_id}</span>
                      <span>chunk_id: {citation.original.chunk_id}</span>
                      {citation.relevance && <span>score: {citation.relevance}</span>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {message.metadata && Object.keys(message.metadata).length > 0 && (
            <div className="debug-block">
              <h4>Response metadata</h4>
              <pre>{JSON.stringify(message.metadata, null, 2)}</pre>
            </div>
          )}
        </div>
      )}

      <div className="sr-only" aria-live="polite">
        {copyKey ? 'Copied to clipboard.' : ''}
      </div>
    </div>
  )
}
