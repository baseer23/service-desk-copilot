import React, { useState } from 'react'

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

export default function MessageBubble({ message }: Props) {
  const label = ROLE_LABEL[message.role]
  const [showCitations, setShowCitations] = useState(false)

  const hasCitations = message.role === 'assistant' && (message.citations?.length ?? 0) > 0

  return (
    <div className={`bubble ${message.role}${message.pending ? ' pending' : ''}${message.error ? ' error' : ''}`}>
      <div className="bubble-role">{label}</div>
      <div className="bubble-text">{message.text}</div>
      {hasCitations && (
        <div className="citations">
          <button type="button" onClick={() => setShowCitations((prev) => !prev)}>
            Citations ({message.citations?.length ?? 0})
          </button>
          {showCitations && (
            <div className="citation-list">
              {message.citations?.map((citation) => (
                <div key={citation.chunk_id} className="citation-item">
                  <div className="citation-title">
                    [{citation.doc_id}:{citation.chunk_id}] {citation.title ?? 'Untitled'}
                  </div>
                  {citation.snippet && <div className="citation-snippet">{citation.snippet}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
