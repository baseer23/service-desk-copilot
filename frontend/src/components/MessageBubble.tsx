import React from 'react'

export type Message = {
  id: string
  role: 'user' | 'assistant'
  text: string
  ts?: number
}

export default function MessageBubble({ m }: { m: Message }) {
  return (
    <div className={`bubble ${m.role}`}>
      <div>{m.text}</div>
      {m.ts && (
        <div className="timestamp">{new Date(m.ts).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</div>
      )}
    </div>
  )
}

