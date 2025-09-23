import React, { useEffect, useMemo, useRef, useState } from 'react'

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
  const withoutInlineIds = joined
    .replace(/\[doc_id:[^\]]*\]/gi, '')
    .replace(/\[chunk_id:[^\]]*\]/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim()

  if (withoutInlineIds.length > 0) return withoutInlineIds

  const fallback = text
    .replace(/\[doc_id:[^\]]*\]/gi, '')
    .replace(/\[chunk_id:[^\]]*\]/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim()

  return fallback.length > 0 ? fallback : text
}

export default function MessageBubble({ message }: Props) {
  const label = ROLE_LABEL[message.role]
  const [showCitations, setShowCitations] = useState(false)
  const [copyKey, setCopyKey] = useState<string | null>(null)
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

    return message.citations.map((citation) => {
      const snippet = citation.snippet?.trim()
      const base = snippet && snippet.length > 0 ? snippet : displayText
      const safeBase = base && base.length > 0 ? base : 'Preview unavailable.'
      const title = citation.title?.trim() && citation.title.trim().length > 0 ? citation.title.trim() : 'Untitled'
      const shortExcerpt = toSmartQuote(buildExcerpt(safeBase, SHORT_QUOTE_LENGTH))
      const longExcerpt = toSmartQuote(buildExcerpt(safeBase, LONG_QUOTE_LENGTH))
      const rawId = `${citation.doc_id}:${citation.chunk_id}`
      const relevance = Number.isFinite(citation.score) ? citation.score.toFixed(2) : '—'
      return {
        original: citation,
        title,
        shortExcerpt,
        longExcerpt,
        rawId,
        relevance,
        humanLine: `${longExcerpt} — ${title}`,
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
          <div className="bubble-toolbar">
            <button
              type="button"
              className="bubble-action"
              onClick={handleCopyAnswer}
              aria-label="Copy answer"
            >
              {copyKey === 'answer' ? 'Copied' : 'Copy answer'}
            </button>
            {hasCitations && (
              <>
                <button
                  type="button"
                  className="bubble-action"
                  onClick={handleToggleCitations}
                  aria-expanded={showCitations}
                  aria-label={showCitations ? 'Hide citations' : 'Show citations'}
                >
                  {showCitations ? 'Hide citations' : 'Show citations'}
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {hasCitations && !isPending && (
        <div className="citation-summary">
          <ul className="citation-chips">
            {citations.map((citation) => (
              <li key={citation.rawId}>
                <button
                  type="button"
                  className="citation-chip"
                  onClick={() => copyText(citation.rawId, `chip-${citation.rawId}`)}
                  title={citation.rawId}
                  aria-label={`Copy source ID for ${citation.title}`}
                >
                  <span className="citation-chip-quote">{citation.shortExcerpt}</span>
                  <span className="citation-chip-title"> — {citation.title}</span>
                </button>
              </li>
            ))}
          </ul>

          {showCitations && (
            <div className="citation-drawer">
              {citations.map((citation) => (
                <article key={`drawer-${citation.rawId}`} className="citation-entry">
                  <h4 className="citation-entry-title">{citation.title}</h4>
                  <p className="citation-entry-subtitle">{citation.longExcerpt}</p>
                  <div className="citation-entry-meta">
                    <span>Relevance {citation.relevance}</span>
                    <span aria-hidden="true" className="meta-dot">
                      •
                    </span>
                    <button
                      type="button"
                      className="meta-id"
                      onClick={() => copyText(citation.rawId, `meta-${citation.rawId}`)}
                      title={citation.rawId}
                      aria-label={`Copy source ID ${citation.rawId}`}
                    >
                      {copyKey === `meta-${citation.rawId}` ? 'Copied' : 'Source ID'}
                    </button>
                  </div>
                  <div className="citation-entry-actions">
                    <button
                      type="button"
                      onClick={() => copyText(citation.humanLine, `quote-${citation.rawId}`)}
                      aria-label={`Copy quote from ${citation.title}`}
                    >
                      {copyKey === `quote-${citation.rawId}` ? 'Copied' : 'Copy quote'}
                    </button>
                    <button
                      type="button"
                      onClick={() => copyText(citation.rawId, `source-${citation.rawId}`)}
                      aria-label={`Copy source ID ${citation.rawId}`}
                    >
                      {copyKey === `source-${citation.rawId}` ? 'Copied' : 'Copy source ID'}
                    </button>
                  </div>
                </article>
              ))}
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
