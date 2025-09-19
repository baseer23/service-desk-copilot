import React, { useEffect, useMemo, useRef, useState } from 'react'
import Composer from './components/Composer'
import MessageBubble, { Message } from './components/MessageBubble'

const TEST_REPLY = 'hi, this was a test you pass'

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [lastAssistant, setLastAssistant] = useState<string | null>(null)
  const threadRef = useRef<HTMLDivElement | null>(null)

  // Restore last assistant reply for Speak
  useEffect(() => {
    const stored = localStorage.getItem('lastAssistantReply')
    if (stored) {
      setMessages([{ id: 'init-assistant', role: 'assistant', text: stored }])
      setLastAssistant(stored)
    }
  }, [])

  useEffect(() => {
    // auto-scroll to bottom on new message
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight
    }
  }, [messages])

  const onSend = async (text: string, files: File[]) => {
    setLoading(true)
    const ts = Date.now()
    setMessages((prev) => [
      ...prev,
      { id: `u-${ts}`, role: 'user', text, ts },
      { id: `a-${ts}`, role: 'assistant', text: TEST_REPLY, ts: ts + 1 },
    ])
    localStorage.setItem('lastAssistantReply', TEST_REPLY)
    setLastAssistant(TEST_REPLY)
    setLoading(false)
  }

  const speak = () => {
    const toSpeak = lastAssistant || localStorage.getItem('lastAssistantReply')
    if (!toSpeak || !('speechSynthesis' in window)) return
    const u = new SpeechSynthesisUtterance(toSpeak)
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(u)
  }

  return (
    <div className="app-page">
      <div className="app-container">
        <div className="chat-panel">
          <div className="chat-header">
            DeskMate — GraphRAG Service Desk Pilot
          </div>
          <div className="chat-thread" ref={threadRef}>
            {messages.length === 0 && (
              <div className="muted">Ask a question to get started.</div>
            )}
            {messages.map((m) => (
              <MessageBubble key={m.id} m={m} />
            ))}
          </div>
          <Composer onSend={onSend} loading={loading} onSpeak={speak} lastAssistant={lastAssistant} />
        </div>
      </div>
    </div>
  )
}
