import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { request } from './api'
import { mergeActivity, type ActivityEvent, streamInvestigation } from './investigationStream'
import InvestigationActivity from './InvestigationActivity'
import { VoiceAudio } from './voiceAudio'
import { type ReadinessReport } from './readiness'

interface Turn {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system_event'
  text: string
  state?: string
  created_at?: string
}
export function Transcript({ turns }: { turns: Turn[] }): JSX.Element {
  const panel = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const [jump, setJump] = useState(false)
  const latest = () => {
    const el = panel.current
    if (el) el.scrollTop = el.scrollHeight
    nearBottom.current = true
    setJump(false)
  }
  useEffect(() => {
    if (nearBottom.current) latest()
    else setJump(true)
  }, [turns])
  return (
    <div className="voice-transcript-wrap">
      <div
        ref={panel}
        className="voice-transcript"
        role="log"
        aria-label="Support transcript"
        aria-live="polite"
        data-testid="voice-transcript"
        onScroll={() => {
          const el = panel.current
          if (el) nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64
        }}
      >
        {turns.map((turn) => (
          <article
            key={turn.id}
            className={`voice-bubble ${turn.role}`}
            data-testid="voice-message"
          >
            <strong>
              {turn.role === 'assistant' ? 'DeskMate' : turn.role === 'user' ? 'You' : 'Activity'}
            </strong>
            <p>{turn.text}</p>
            {turn.created_at && (
              <time dateTime={turn.created_at}>
                {new Date(turn.created_at).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </time>
            )}
            {turn.state === 'interrupted' && <small>Interrupted</small>}
          </article>
        ))}
      </div>
      {jump && (
        <button className="voice-jump button button-subtle" onClick={latest}>
          Jump to latest
        </button>
      )}
    </div>
  )
}

export default function VoiceSupport({
  onCaseCreated,
  readiness: _suppliedReadiness,
}: {
  onCaseCreated: (caseId: string) => void | Promise<void>
  readiness?: ReadinessReport | null
}): JSX.Element {
  const [available, setAvailable] = useState(false)
  const [checking, setChecking] = useState(false)
  const [status, setStatus] = useState('idle')
  const [stage, setStage] = useState('')
  const [error, setError] = useState('')
  const [muted, setMuted] = useState(false)
  const [inputLevel, setInputLevel] = useState(0)
  const [deviceId, setDeviceId] = useState('')
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [turns, setTurns] = useState<Turn[]>([])
  const [partial, setPartial] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const investigation = useRef<AbortController | null>(null)
  const [caseId, setCaseId] = useState<string | null>(null)
  const [usage, setUsage] = useState({
    listening_seconds: 0,
    connected_seconds: 0,
    estimated_cad: 0,
  })
  const audio = useRef<VoiceAudio | null>(null)
  const socket = useRef<WebSocket | null>(null)
  const active = useRef(false)
  const mounted = useRef(true)
  const attempt = useRef(0)
  const seen = useRef(new Set<string>())
  const retry = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const connectingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const started = useRef(0)
  const elapsedUsage = useRef({ connected_seconds: 0, estimated_cad: 0 })
  const connectionUsage = useRef({ connected_seconds: 0, estimated_cad: 0 })
  const lastMeter = useRef(0)
  const lastRequest = useRef<{ id: string; transient: boolean; needsInput: boolean } | null>(null)

  const refresh = useCallback(async () => {
    setChecking(true)
    try {
      const report = await request<{ ready: boolean }>('/speech/readiness')
      if (mounted.current) setAvailable(report.ready === true)
    } catch {
      if (mounted.current) setAvailable(false)
    } finally {
      if (mounted.current) setChecking(false)
    }
  }, [])

  const cleanup = useCallback(() => {
    attempt.current++
    active.current = false
    clearTimeout(reconnectTimer.current)
    clearTimeout(connectingTimer.current)
    audio.current?.close()
    audio.current = null
    const ws = socket.current
    socket.current = null
    ws?.close()
  }, [])
  useEffect(() => {
    mounted.current = true
    void refresh()
    void navigator.mediaDevices
      ?.enumerateDevices()
      .then((all) => {
        if (mounted.current) setDevices(all.filter((device) => device.kind === 'audioinput'))
      })
      .catch(() => undefined)
    const disabled = () => {
      cleanup()
      setStatus('stopped')
      void refresh()
    }
    window.addEventListener('azure-usage-disabled', disabled)
    return () => {
      mounted.current = false
      investigation.current?.abort()
      cleanup()
      window.removeEventListener('azure-usage-disabled', disabled)
    }
  }, [cleanup, refresh])
  useEffect(() => {
    const timer = setInterval(() => {
      if (active.current)
        setUsage((old) => ({
          ...old,
          listening_seconds: Math.floor((Date.now() - started.current) / 1000),
        }))
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  function fail(message: string) {
    cleanup()
    if (mounted.current) {
      setStatus('error')
      setError(message)
      setStage('')
      setPartial('')
      setInputLevel(0)
    }
  }
  function stop() {
    active.current = false
    attempt.current++
    clearTimeout(reconnectTimer.current)
    clearTimeout(connectingTimer.current)
    audio.current?.close()
    audio.current = null
    const ws = socket.current
    setStage('')
    setPartial('')
    setInputLevel(0)
    if (ws?.readyState === WebSocket.OPEN) {
      // Release local capture immediately, but wait for SDK stop/usage settlement
      // before another recording can reserve the single recognition slot.
      setStatus('stopping')
      connectingTimer.current = setTimeout(() => {
        if (socket.current === ws)
          fail(
            'The microphone stopped, but recognition shutdown could not be confirmed. Refresh status before restarting.',
          )
      }, 10000)
      ws.send(JSON.stringify({ type: 'stop' }))
    } else {
      socket.current = null
      ws?.close()
      setStatus('stopped')
    }
  }
  function connect(currentAttempt: number) {
    if (!active.current || attempt.current !== currentAttempt) return
    const url = new URL('/speech/stream', import.meta.env.VITE_API_BASE || location.origin)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(url)
    socket.current = ws
    connectionUsage.current = { connected_seconds: 0, estimated_cad: 0 }
    ws.onmessage = (event) => {
      if (socket.current !== ws || !mounted.current) return
      try {
        const value = JSON.parse(event.data) as {
          type: string
          state?: string
          id?: string
          text?: string
          message?: string
          connected_seconds?: number
          estimated_cad?: number
        }
        if (
          typeof value.connected_seconds === 'number' &&
          typeof value.estimated_cad === 'number'
        ) {
          connectionUsage.current = {
            connected_seconds: value.connected_seconds,
            estimated_cad: value.estimated_cad,
          }
          setUsage((old) => ({
            ...old,
            connected_seconds:
              elapsedUsage.current.connected_seconds + connectionUsage.current.connected_seconds,
            estimated_cad:
              elapsedUsage.current.estimated_cad + connectionUsage.current.estimated_cad,
          }))
        }
        if (value.type === 'state') {
          if (value.state === 'stopped') {
            active.current = false
            clearTimeout(connectingTimer.current)
            audio.current?.close()
            audio.current = null
            socket.current = null
            ws.close()
            setStatus('stopped')
            setStage('')
            setPartial('')
            setInputLevel(0)
            return
          }
          if (value.state === 'error') {
            fail('Speech recognition stopped. Refresh status and try again.')
            return
          }
          if (!active.current) return
          if (value.state === 'listening') {
            clearTimeout(connectingTimer.current)
            retry.current = 0
            setStage('')
          }
          setStatus(value.state ?? 'connecting')
        } else if (value.type === 'transcript.partial') {
          if (!/^\s*stop[.!?,;:]*\s*$/i.test(value.text ?? '')) setPartial(value.text ?? '')
        } else if (value.type === 'transcript.final') {
          const recognized = value.text?.trim()
          if (!recognized || !value.id || seen.current.has(value.id)) return
          if (/^stop[.!?,;:]*$/i.test(recognized)) {
            stop()
            return
          }
          seen.current.add(value.id)
          setPartial('')
          setText((old) => (old ? `${old} ${recognized}` : recognized))
          setTurns((old) => [
            ...old,
            {
              id: value.id!,
              role: 'user',
              text: recognized,
              state: 'final',
              created_at: new Date().toISOString(),
            },
          ])
        } else if (value.type === 'error')
          fail(value.message ?? 'Speech recognition stopped. Please try again.')
      } catch {
        fail('The recognition connection returned an invalid message. Please try again.')
      }
    }
    ws.onclose = (event) => {
      if (socket.current !== ws || !mounted.current) return
      socket.current = null
      clearTimeout(connectingTimer.current)
      elapsedUsage.current.connected_seconds += connectionUsage.current.connected_seconds
      elapsedUsage.current.estimated_cad += connectionUsage.current.estimated_cad
      if (!active.current) {
        setStatus('stopped')
        return
      }
      if (event.code === 1008 || event.code === 1011 || event.code === 1000) {
        fail('Speech recognition stopped. Refresh status and try again.')
        return
      }
      if (++retry.current > 5) {
        fail('The speech connection could not recover. Your transcript is preserved.')
        return
      }
      setStatus('reconnecting')
      reconnectTimer.current = setTimeout(
        () => connect(currentAttempt),
        Math.min(8000, 500 * 2 ** (retry.current - 1)),
      )
    }
    ws.onerror = () => {
      /* close determines whether this interruption can recover */
    }
    connectingTimer.current = setTimeout(() => {
      if (socket.current === ws) ws.close()
    }, 25000)
  }
  async function start() {
    if (active.current || socket.current !== null || busy || !available) return
    cleanup()
    const currentAttempt = ++attempt.current
    active.current = true
    retry.current = 0
    seen.current.clear()
    elapsedUsage.current = { connected_seconds: 0, estimated_cad: 0 }
    connectionUsage.current = { connected_seconds: 0, estimated_cad: 0 }
    started.current = Date.now()
    setUsage({ listening_seconds: 0, connected_seconds: 0, estimated_cad: 0 })
    setError('')
    setStatus('connecting')
    setMuted(false)
    setStage('Preparing session')
    const capture = new VoiceAudio()
    audio.current = capture
    try {
      await capture.start(
        (frame) => {
          const ws = socket.current
          if (!active.current || ws?.readyState !== WebSocket.OPEN) return
          if (ws.bufferedAmount > 65536) {
            fail('The microphone connection is too slow. Your transcript is preserved.')
            return
          }
          ws.send(frame)
        },
        (next) => {
          if (mounted.current && currentAttempt === attempt.current) setStage(next)
        },
        (level) => {
          if (Date.now() - lastMeter.current > 100 && mounted.current) {
            setInputLevel(level)
            lastMeter.current = Date.now()
          }
        },
        deviceId || undefined,
        () =>
          fail(
            'The microphone disconnected. Your transcript is preserved. Reconnect the microphone and try again.',
          ),
      )
      if (!mounted.current || currentAttempt !== attempt.current || !active.current) {
        capture.close()
        return
      }
      void navigator.mediaDevices
        ?.enumerateDevices()
        .then((all) => setDevices(all.filter((device) => device.kind === 'audioinput')))
        .catch(() => undefined)
      setStage('Connecting')
      connect(currentAttempt)
    } catch (e) {
      if (mounted.current && currentAttempt === attempt.current) fail((e as Error).message)
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!text.trim() || busy || active.current) return
    setBusy(true)
    setError('')
    setActivity([])
    const controller = new AbortController()
    investigation.current = controller
    const progress = (event: ActivityEvent) => {
      if (mounted.current) setActivity((old) => mergeActivity(old, event))
    }
    try {
      const previous = lastRequest.current
      const result = previous?.needsInput
        ? await streamInvestigation<{
            case_id?: string
            request_id?: string
            result?: Record<string, unknown>
            [key: string]: unknown
          }>(
            `/${previous.transient ? 'requests' : 'cases'}/${previous.id}/resume`,
            { answer: text.trim() },
            progress,
            controller.signal,
          )
        : await streamInvestigation<{
            case_id: string | null
            request_id: string
            result: Record<string, unknown>
          }>('/ask', { summary: text.trim() }, progress, controller.signal)
      const state = (result.result ?? result) as {
        pending_question?: string
        final_answer?: string
        recommended_next_action?: string
        status?: string
        final_response?: { answer?: string }
      }
      const answer =
        state.pending_question ??
        state.final_response?.answer ??
        state.final_answer ??
        state.recommended_next_action ??
        'The investigation completed without a verified recommendation. Review the evidence in Chat.'
      setTurns((old) => [
        ...old,
        {
          id: `answer-${Date.now()}`,
          role: 'assistant',
          text: answer,
          created_at: new Date().toISOString(),
        },
      ])
      const id = String(result.case_id ?? result.request_id ?? previous?.id ?? '')
      setCaseId(result.case_id ? String(result.case_id) : null)
      lastRequest.current = {
        id,
        transient: !result.case_id,
        needsInput: state.status === 'needs_input',
      }
      setText('')
    } catch (e) {
      if (mounted.current)
        setError(
          (e as Error).name === 'AbortError'
            ? 'Investigation cancelled. Your transcript is preserved.'
            : (e as Error).message,
        )
    } finally {
      if (mounted.current) setBusy(false)
      investigation.current = null
    }
  }
  const connected = ['listening', 'reconnecting', 'connecting'].includes(status)
  const displayStatus =
    status === 'idle'
      ? 'Ready to talk'
      : muted && status === 'listening'
        ? 'Microphone muted'
        : status[0].toUpperCase() + status.slice(1)
  const transcriptTurns = partial
    ? [...turns, { id: 'partial', role: 'user' as const, text: partial, state: 'partial' }]
    : turns
  const duration = `${Math.floor(usage.listening_seconds / 60)}:${String(usage.listening_seconds % 60).padStart(2, '0')}`
  return (
    <section className="voice-support card" aria-label="Voice Support">
      <header className="voice-heading">
        <div>
          <span className="eyebrow">TALK TO DESKMATE</span>
          <h2>Voice Support</h2>
        </div>
      </header>
      {(!available || error) && (
        <div className="readiness-summary" aria-label="Support availability">
          <button
            type="button"
            className="button button-subtle"
            disabled={checking || connected}
            onClick={() => void refresh()}
          >
            {checking ? 'Checking status…' : 'Refresh status'}
          </button>
        </div>
      )}
      {!available && !checking && (
        <p className="readiness-note">
          Speech recognition is unavailable. You can still type your request below.
        </p>
      )}
      <div className="voice-center">
        <button
          type="button"
          className={`microphone-orb ${status === 'listening' && !muted ? 'listening' : ''}`}
          aria-label={
            connected ? (muted ? 'Unmute microphone' : 'Mute microphone') : 'Start voice chat'
          }
          aria-pressed={connected ? muted : undefined}
          disabled={
            (!connected && !available) || busy || status === 'connecting' || status === 'stopping'
          }
          onClick={() => {
            if (!connected) void start()
            else {
              audio.current?.setMuted(!muted)
              setMuted(!muted)
              setInputLevel(0)
            }
          }}
        >
          <svg
            viewBox="0 0 24 24"
            width="34"
            height="34"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            aria-hidden="true"
          >
            <rect x="9" y="2" width="6" height="12" rx="3" />
            <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" />
          </svg>
        </button>
        <div
          className="microphone-meter"
          role="meter"
          aria-label="Microphone activity"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(Math.min(1, inputLevel * 8) * 100)}
        >
          {Array.from({ length: 15 }, (_, i) => (
            <i
              key={i}
              style={{
                height: `${6 + Math.min(1, inputLevel * 8) * (30 - Math.abs(7 - i) * 3)}px`,
              }}
            />
          ))}
        </div>
        <div className="voice-state">
          <span
            className={`status-dot ${status === 'listening' ? 'active' : ''}`}
            aria-hidden="true"
          />
          <p role="status" data-testid="voice-status">
            {displayStatus}
          </p>
        </div>
        <p className="muted">
          Speak continuously. Say “Stop” on its own or click Stop when finished. Responses appear as
          text.
        </p>
        <p aria-label="Speech usage">
          Listening {duration} · Connected speech {usage.connected_seconds.toFixed(0)}s · Estimated
          CAD ${usage.estimated_cad.toFixed(4)}
        </p>
        <p className="readiness-note">
          Connected recognition includes pauses, silence and mute time. This estimate is not an
          invoice or billing cap.
        </p>
        {!connected && (
          <>
            <label htmlFor="microphone-device">Microphone</label>
            <select
              id="microphone-device"
              value={deviceId}
              onChange={(event) => setDeviceId(event.target.value)}
            >
              <option value="">System default</option>
              {devices
                .filter((device) => device.deviceId !== 'default')
                .map((device, i) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label || `Microphone ${i + 1}`}
                  </option>
                ))}
            </select>
          </>
        )}
      </div>
      {stage && connected && (
        <div className="connection-progress">
          <p aria-live="polite">{stage}</p>
        </div>
      )}
      {error && (
        <div className="voice-error" role="alert">
          <p>{error}</p>
        </div>
      )}
      {!!transcriptTurns.length && <Transcript turns={transcriptTurns} />}
      <InvestigationActivity
        events={activity}
        busy={busy}
        onCancel={() => investigation.current?.abort()}
      />
      <div className="voice-controls">
        <div className="voice-button-row">
          <button
            type="button"
            className="button button-secondary"
            disabled={connected || status === 'stopping' || busy || !available}
            onClick={() => void start()}
          >
            Ready to Talk
          </button>
          {connected && (
            <>
              <button
                type="button"
                className="button button-toggle"
                aria-pressed={muted}
                onClick={() => {
                  audio.current?.setMuted(!muted)
                  setMuted(!muted)
                  setInputLevel(0)
                }}
              >
                {muted ? 'Unmute' : 'Mute'}
              </button>
              <button type="button" className="button button-danger" onClick={stop}>
                Stop session
              </button>
            </>
          )}
          {caseId && (
            <button
              type="button"
              className="button button-success"
              onClick={() => void onCaseCreated(caseId)}
            >
              Open investigation
            </button>
          )}
        </div>
        <form className="voice-composer" onSubmit={submit}>
          <label htmlFor="voice-text">Message DeskMate</label>
          <div>
            <textarea
              id="voice-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Your recognized text appears here. You can also type…"
              maxLength={20000}
              disabled={connected || status === 'stopping' || busy}
            />
            <button
              className="button button-send"
              disabled={connected || status === 'stopping' || busy || !text.trim()}
            >
              {busy ? 'Investigating…' : 'Send message'}
            </button>
          </div>
        </form>
      </div>
    </section>
  )
}
