import React, { useEffect, useMemo, useRef, useState } from 'react'

type Props = {
  onSend: (text: string, files: File[]) => Promise<void> | void
  loading?: boolean
  onSpeak?: () => void
  lastAssistant?: string | null
}

declare global {
  interface Window {
    webkitSpeechRecognition?: any
    SpeechRecognition?: any
  }
}

export default function Composer({ onSend, loading, onSpeak, lastAssistant }: Props) {
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [recording, setRecording] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const recRef = useRef<any>(null)

  const speechSupported = typeof window !== 'undefined' && (
    'webkitSpeechRecognition' in window || 'SpeechRecognition' in window
  )

  useEffect(() => {
    if (!recording) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        stopRecording()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [recording])

  const startRecording = () => {
    if (!speechSupported || recording) return
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition
    const rec = new Rec()
    rec.continuous = false
    rec.interimResults = true
    rec.lang = navigator.language || 'en-US'
    rec.onresult = (event: any) => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript
        if (event.results[i].isFinal) {
          setText((t) => (t ? t + ' ' : '') + transcript)
        } else {
          interim += transcript
        }
      }
      if (interim && inputRef.current) {
        // Show interim by setting placeholder-like look via value; we keep it simple and ignore interim in UI.
      }
    }
    rec.onerror = () => setRecording(false)
    rec.onend = () => setRecording(false)
    rec.start()
    recRef.current = rec
    setRecording(true)
  }

  const stopRecording = () => {
    if (recRef.current) {
      try { recRef.current.stop() } catch {}
    }
    setRecording(false)
  }

  const onFileChange: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const list = e.currentTarget.files
    if (!list) return
    const newFiles = Array.from(list)
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name + f.size + f.lastModified))
      const merged = [...prev]
      for (const f of newFiles) {
        const key = f.name + f.size + f.lastModified
        if (!names.has(key)) merged.push(f)
      }
      return merged
    })
    // keep selection; do not clear
    e.currentTarget.value = ''
  }

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx))
  }

  const trySend = async () => {
    const trimmed = text.trim()
    if (!trimmed || loading) return
    await onSend(trimmed, files)
    setText('') // clear input after send; keep file chips
    inputRef.current?.focus()
  }

  const onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void trySend()
    }
  }

  return (
    <div className="composer">
      <div className="composer-row">
        <textarea
          ref={inputRef}
          className="composer-input"
          placeholder="Type your service desk question…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          data-testid="composer-input"
          aria-label="Message input"
        />
        <button
          className="btn icon"
          onClick={onSpeak}
          data-testid="speak-btn"
          aria-label="Speak last assistant reply"
          title={('speechSynthesis' in window) ? 'Speak last reply' : 'Speech synthesis not supported'}
          disabled={!('speechSynthesis' in window) || !lastAssistant}
        >
          🔊
        </button>
        <button
          className="btn icon"
          title={speechSupported ? (recording ? 'Stop microphone' : 'Start microphone') : 'Voice input not supported'}
          aria-label={recording ? 'Stop microphone' : 'Start microphone'}
          onClick={speechSupported ? (recording ? stopRecording : startRecording) : undefined}
          disabled={!speechSupported}
        >
          {recording ? '⏹' : '🎤'}
        </button>
        <label className="btn icon" title="Attach files" aria-label="Attach files">
          📎
          <input type="file" multiple onChange={onFileChange} style={{ display: 'none' }} />
        </label>
        <button
          className="btn primary"
          onClick={trySend}
          disabled={loading}
          data-testid="send-btn"
          aria-label="Send message"
        >
          {loading ? 'Thinking…' : 'Send'}
        </button>
      </div>
      {files.length > 0 && (
        <div className="file-chips">
          {files.map((f, i) => (
            <span key={i} className="chip" title={`${f.name} (${Math.round(f.size/1024)} KB)`}>
              {f.name}
              <span className="x" onClick={() => removeFile(i)} aria-label={`Remove ${f.name}`}>×</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
