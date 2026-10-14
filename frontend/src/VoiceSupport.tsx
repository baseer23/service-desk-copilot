import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { post, request } from './api'
import { VoiceAudio } from './voiceAudio'
import { type ReadinessReport, useReadiness } from './readiness'
import { VoiceClientError, visibleVoiceError } from './voiceErrors'

interface Turn {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system_event'
  text: string
  state?: string
  created_at?: string
}
interface Conversation {
  session_id: string
  status: string
  turns: Turn[]
  case_id: string | null
  resolution_status: string
  mode?: 'voice' | 'text'
  failure_category?: string | null
}
interface Envelope {
  version: number
  type: string
  session_id: string
  sequence: number
  payload: Record<string, unknown>
}
const terminal = new Set(['completed', 'stopped', 'failed'])

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
  readiness: suppliedReadiness,
}: {
  onCaseCreated: (caseId: string) => void | Promise<void>
  readiness?: ReadinessReport | null
}): JSX.Element {
  const { readiness, refresh, checking } = useReadiness(suppliedReadiness)
  const demo = readiness?.runtime_mode === 'isolated_demo'
  const [stage, setStage] = useState('')
  const [saving, setSaving] = useState(false)
  const [microphoneActive, setMicrophoneActive] = useState(false)
  const [microphoneRequested, setMicrophoneRequested] = useState(false)
  const [inputLevel, setInputLevel] = useState(0)
  const [captureReady, setCaptureReady] = useState(false)
  const [delivered, setDelivered] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [deviceId, setDeviceId] = useState('')
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const lastCapture = useRef(0)
  const lastMeter = useRef(0)
  const attempt = useRef(0)
  const explicitlyStopped = useRef(false)
  const connectionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const readyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const heartbeatTimer = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [muted, setMuted] = useState(false)
  const [text, setText] = useState('')
  const [activity, setActivity] = useState<string[]>([])
  const socket = useRef<WebSocket | null>(null)
  const audio = useRef<VoiceAudio | null>(null)
  const starting = useRef(false)
  const mounted = useRef(true)
  const sequence = useRef(0)
  const serverSequence = useRef(0)
  const activeId = useRef('')
  const streamReady = useRef(false)
  const playback = useRef(Promise.resolve())
  const playbackEpoch = useRef(0)
  const audioQueue = useRef({ pending: 0 })

  const cleanup = useCallback(() => {
    attempt.current++
    clearTimeout(connectionTimer.current)
    clearTimeout(readyTimer.current)
    clearInterval(heartbeatTimer.current)
    playbackEpoch.current++
    audioQueue.current = { pending: 0 }
    playback.current = Promise.resolve()
    streamReady.current = false
    lastCapture.current = 0
    audio.current?.close()
    audio.current = null
    const ws = socket.current
    socket.current = null
    ws?.close()
    starting.current = false
  }, [])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      cleanup()
    }
  }, [cleanup])
  useEffect(() => {
    const timer = window.setInterval(() => {
      setCaptureReady(!!audio.current?.capturing && Date.now() - lastCapture.current < 2000)
      setSpeaking(!!audio.current?.playback.size)
    }, 500)
    return () => window.clearInterval(timer)
  }, [])
  async function listMicrophones() {
    try {
      const available = await navigator.mediaDevices?.enumerateDevices()
      if (mounted.current) setDevices((available ?? []).filter(device => device.kind === 'audioinput'))
    } catch { /* Device selection is optional; capture errors remain explicit. */ }
  }
  useEffect(() => { void listMicrophones() }, [])

  function fail(error: unknown) {
    const visible = visibleVoiceError(error)
    setError(visible.message)

    cleanup()
    setMicrophoneActive(false)
    setInputLevel(0)
    setStatus('failed')
  }
  function send(type: string, payload: Record<string, unknown> = {}) {
    const ws = socket.current
    if (type === 'audio.append' && !streamReady.current) return false
    if (ws?.readyState !== WebSocket.OPEN) return false
    if (ws.bufferedAmount > 262144) {
      fail(
        new VoiceClientError(
          'AUDIO_CONNECTION_SLOW',
          'Audio connection is too slow. Reconnect or use text.',
        ),
      )
      return false
    }
    ws.send(
      JSON.stringify({
        version: 1,
        type,
        session_id: activeId.current,
        sequence: ++sequence.current,
        payload,
      }),
    )
    return true
  }
  function merge(id: string, role: Turn['role'], value: string, partial: boolean) {
    setTurns((old) => {
      const index = old.findIndex((turn) => turn.id === id)
      if (index < 0)
        return [
          ...old,
          {
            id,
            role,
            text: value,
            state: partial ? 'partial' : 'final',
            created_at: new Date().toISOString(),
          },
        ]
      return old.map((turn, i) =>
        i === index
          ? {
              ...turn,
              text: partial ? turn.text + value : value,
              state: partial ? 'partial' : 'final',
            }
          : turn,
      )
    })
  }

  function connect(id: string) {
    activeId.current = id
    sequence.current = 0
    serverSequence.current = 0
    const base = import.meta.env.VITE_API_BASE || location.origin
    const url = new URL(`/voice/sessions/${id}/stream`, base)
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(url)
    socket.current = ws
    ws.onmessage = (event) => {
      if (socket.current !== ws || !mounted.current) return
      try {
        const message = JSON.parse(event.data) as Envelope
        if (
          message.version !== 1 ||
          message.session_id !== id ||
          message.sequence <= serverSequence.current
        )
          return
        if (message.sequence !== serverSequence.current + 1)
          throw new VoiceClientError(
            'VOICE_SEQUENCE_ERROR',
            'Voice messages arrived out of order. Please reconnect.',
          )
        serverSequence.current = message.sequence
        const p = message.payload
        if (message.type === 'session.snapshot') {
          const state = p.conversation as Conversation
          streamReady.current = !terminal.has(state.status)
          clearTimeout(readyTimer.current)
          setStage('')
          setConversation(state)
          setTurns(state.turns)
          setStatus(state.status)
          if (terminal.has(state.status)) {
            audio.current?.stopCapture()
            setMicrophoneActive(false)
          }
        } else if (message.type === 'audio.delivery') setDelivered(Number(p.frames) > 0)
        else if (message.type === 'status') setStatus(String(p.status))
        else if (message.type.startsWith('transcript.'))
          merge(
            String(p.turn_id),
            message.type.includes('.user.') ? 'user' : 'assistant',
            String(p.text),
            message.type.endsWith('.partial'),
          )
        else if (message.type === 'activity')
          setActivity((old) => [...old.slice(-99), String(p.label)])
        else if (message.type === 'playback.clear') {
          playbackEpoch.current++
          audio.current?.clearPlayback()
        } else if (message.type === 'audio') {
          const queue = audioQueue.current
          if (++queue.pending > 64)
            throw new VoiceClientError(
              'AUDIO_QUEUE_FULL',
              'Audio playback queue is full. Please reconnect.',
            )
          const epoch = playbackEpoch.current
          const player = audio.current
          playback.current = playback.current
            .then(async () => {
              if (epoch === playbackEpoch.current) await player?.play(String(p.audio))
            })
            .catch(() => {
              if (socket.current !== ws || audio.current !== player || !mounted.current) return
              fail(
                new VoiceClientError(
                  'AUDIO_PLAYBACK_FAILED',
                  'Audio playback failed. Please use text or reconnect.',
                ),
              )
            })
            .finally(() => {
              queue.pending--
            })
        } else if (message.type === 'error')
          throw new VoiceClientError(
            typeof p.code === 'string' ? p.code : 'VOICE_CONNECTION_FAILED',
            'The support connection ended. Reconnect through text or start a new session.',
          )
      } catch (e) {
        fail(e)
      }
    }
    ws.onopen = () => {
      if (socket.current !== ws) return
      clearTimeout(connectionTimer.current)
      setStage('Connected')
      send('ping')
      heartbeatTimer.current = setInterval(() => { if (socket.current === ws) send('ping') }, 20000)
      readyTimer.current = setTimeout(() => {
        if (socket.current === ws && !streamReady.current)
          fail(
            new VoiceClientError(
              'VOICE_PROVIDER_TIMEOUT',
              'The assistant did not become ready. Refresh status and reconnect through text.',
            ),
          )
      }, 105000)
    }
    connectionTimer.current = setTimeout(() => {
      if (socket.current === ws && ws.readyState !== WebSocket.OPEN)
        fail(
          new VoiceClientError(
            'VOICE_WS_TIMEOUT',
            'The voice WebSocket could not connect. Check the server and use the same localhost origin.',
          ),
        )
    }, 10000)
    ws.onclose = (event) => {
      if (socket.current !== ws || !mounted.current) return
      clearTimeout(connectionTimer.current)
      clearTimeout(readyTimer.current)
      clearInterval(heartbeatTimer.current)
      socket.current = null
      streamReady.current = false
      audio.current?.finish()
      starting.current = false
      setMicrophoneActive(false)
      setStatus((old) => (terminal.has(old) ? old : 'stopped'))
      if (event.code === 1008)
        fail(
          new VoiceClientError(
            event.reason === 'VOICE_SESSION_TERMINAL' ? event.reason : 'VOICE_WS_AUTHORIZATION',
            'The session could not connect. Start a new session using the same localhost origin.',
          ),
        )
      else if (event.code !== 1000)
        fail(
          new VoiceClientError(
            'VOICE_WS_DISCONNECTED',
            'The support connection was interrupted. Reconnect through text or start a new session.',
          ),
        )
      else setStage('')
    }
    ws.onerror = () => {
      if (socket.current === ws)
        fail(
          new VoiceClientError(
            'VOICE_WS_CONNECT_FAILED',
            'The voice WebSocket could not connect. Check the backend and use one localhost origin consistently.',
          ),
        )
    }
  }

  async function start(withMicrophone: boolean, resume = false) {
    if (starting.current || socket.current) return
    if (!(withMicrophone ? readiness?.voice_ready : readiness?.text_ready)) return
    starting.current = true
    const currentAttempt = ++attempt.current
    explicitlyStopped.current = false
    if (!resume) activeId.current = ''
    setError('')

    setStatus('connecting')
    setMicrophoneRequested(withMicrophone)
    setStage('Preparing session')
    setMuted(false)
    setDelivered(false)
    setCaptureReady(false)
    setInputLevel(0)
    audio.current?.close()
    audio.current = new VoiceAudio()
    try {
      if (withMicrophone) {
        await audio.current.start(
          (data) => send('audio.append', { audio: data }),
          (next) => {
            if (attempt.current === currentAttempt && mounted.current) setStage(next)
          },
          (level) => {
            lastCapture.current = Date.now()
            if (Date.now() - lastMeter.current >= 100 && mounted.current) {
              setInputLevel(level)
              lastMeter.current = Date.now()
            }
          },
          deviceId || undefined,
        )
        setMicrophoneActive(!!audio.current?.stream)
        void listMicrophones()
      }
      if (!mounted.current || attempt.current !== currentAttempt || !starting.current) return
      setStage('Connecting')
      const state = await request<Conversation>(
        '/voice/sessions',
        post(
          resume && conversation
            ? { resume_session_id: conversation.session_id, mode: withMicrophone ? 'voice' : 'text' }
            : { mode: withMicrophone ? 'voice' : 'text' },
        ),
      )
      if (!mounted.current || attempt.current !== currentAttempt || !starting.current) {
        void request(`/voice/sessions/${state.session_id}/stop`, post()).catch(() => undefined)
        return
      }
      if (state.failure_category === 'explicit_stop' || state.status === 'completed')
        throw new VoiceClientError(
          'VOICE_SESSION_TERMINAL',
          'This session has stopped. Start a new session.',
        )
      setConversation(state)
      setTurns(state.turns)
      connect(state.session_id)
    } catch (error) {
      if (mounted.current && attempt.current === currentAttempt) fail(error)
    }
  }
  async function stop() {
    explicitlyStopped.current = true
    const id = activeId.current
    send('session.stop')
    cleanup()
    setStatus('stopped')
    setStage('')
    setMicrophoneActive(false)
    setInputLevel(0)
    if (id)
      try {
        const state = await request<Conversation>(`/voice/sessions/${id}/stop`, post())
        setConversation(state)
      } catch {
        setError('Connection closed. The server will also expire the session automatically.')

      }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!text.trim()) return
    send('text.input', { text: text.trim() })
    setText('')
  }
  async function save() {
    setSaving(true)
    try {
      const result = await request<{ case_id: string }>(
        `/voice/sessions/${activeId.current}/create-case`,
        post(),
      )
      await onCaseCreated(result.case_id)
    } catch {
      setError('The case summary could not be saved. Confirm your contact details first.')

    } finally {
      setSaving(false)
    }
  }
  const connected = !terminal.has(status) && status !== 'idle' && status !== 'connecting'
  const busy = status === 'connecting'
  const listeningReady = streamReady.current && captureReady && delivered && microphoneActive
  const displayStatus = terminal.has(status) ? status[0].toUpperCase() + status.slice(1) :
    status === 'idle' ? 'Ready to talk' :
    muted && microphoneActive ? 'Microphone muted' :
    busy || !streamReady.current ? 'Connecting' :
    speaking ? 'Speaking' :
    status === 'assistant_speaking' ? 'Thinking' :
    ['processing', 'using_tools'].includes(status) ? 'Thinking' :
    microphoneRequested ? (listeningReady ? 'Listening' : 'Connecting') : 'Ready for messages'
  const canReconnect =
    conversation &&
    !explicitlyStopped.current &&
    conversation.failure_category !== 'explicit_stop' &&
    conversation.status !== 'completed' &&
    terminal.has(status)
  const stages = [
    'Preparing session',
    ...(microphoneRequested ? ['Requesting microphone permission', 'Opening microphone'] : []),
    'Connecting',
    'Connected',
    'Listening',
  ]
  return (
    <section className="voice-support card" aria-label="Voice Support">
      <header className="voice-heading">
        <div>
          <span className="eyebrow">TALK TO DESKMATE</span>
          <h2>Voice Support</h2>
        </div>
      </header>
      {demo && (
        <div className="demo-banner">
          <strong>ISOLATED DEMO</strong>
          <p>Responses and audio are simulated in this sample session.</p>
          <p>
            Use text support. Test microphone checks capture and transport only; it will not
            recognize speech or speak.
          </p>
        </div>
      )}
      {(error || !readiness?.voice_ready) && <div className="readiness-summary" aria-label="Support availability">
        <button type="button" className="button button-subtle" disabled={checking || busy}
          onClick={() => void refresh()}>{checking ? 'Checking status…' : 'Refresh status'}</button>
      </div>}
      {readiness && !readiness.voice_ready && <p className="readiness-note">Voice support is temporarily unavailable. You can save your request and search knowledge.</p>}
      {readiness && !readiness.text_ready && <p className="readiness-note">Text support is temporarily unavailable. You can save your request and return later.</p>}
      {!readiness && <p className="readiness-note">Support availability could not be checked. Refresh status or try again later.</p>}
      <div className="voice-center">
        <button type="button" className={`microphone-orb ${listeningReady && !muted ? 'listening' : ''}`}
          aria-label={connected ? (muted ? 'Unmute microphone' : 'Mute microphone') : demo ? 'Test microphone' : 'Start voice chat'}
          aria-pressed={connected ? muted : undefined}
          disabled={busy || (!connected && !readiness?.voice_ready) || (connected && !microphoneActive)}
          onClick={() => {
            if (!connected) void start(true)
            else { audio.current?.setMuted(!muted); setMuted(!muted); setInputLevel(0) }
          }}>
          <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg>
        </button>
        <div className="microphone-meter" role="meter" aria-label="Microphone activity"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(Math.min(1, inputLevel * 8) * 100)}>
          {Array.from({ length: 15 }, (_, i) => <i key={i} style={{ height: `${6 + Math.min(1, inputLevel * 8) * (30 - Math.abs(7 - i) * 3)}px` }} />)}
        </div>
      <div className="voice-state">
        <span className={`status-dot ${connected ? 'active' : ''}`} aria-hidden="true" />
        <p role="status" data-testid="voice-status">
          {displayStatus}
        </p>
      </div>
      {!connected && !busy && <p className="muted">Start a conversation. I’m here to help.</p>}
      {microphoneRequested && listeningReady && !muted && inputLevel < 0.001 && <p className="muted">Speak naturally. The microphone meter moves when sound is captured.</p>}
      {!connected && <><label htmlFor="microphone-device">Microphone</label><select id="microphone-device" value={deviceId} disabled={busy} onChange={event => setDeviceId(event.target.value)}><option value="">System default</option>{devices.filter(device => device.deviceId !== 'default').map((device, i) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Microphone ${i + 1}`}</option>)}</select></>}
      </div>
      {stage && (
        <div className="connection-progress">
          <p aria-live="polite">{stage}</p>
          <ol className="sr-only" aria-label="Connection progress">
            {stages.map((label, index) => (
              <li
                key={label}
                aria-current={label === stage ? 'step' : undefined}
                data-state={
                  index < stages.indexOf(stage) ? 'done' : label === stage ? 'current' : 'pending'
                }
              >
                {label}
              </li>
            ))}
          </ol>
        </div>
      )}
      {error && (
        <div className="voice-error" role="alert">
          <p>{error}</p>

        </div>
      )}
      {conversation && <Transcript turns={turns} />}
      {!turns.length && conversation && (
        <p className="voice-empty">
          Your conversation will appear above. Start text support, or{' '}
          {demo ? 'test the microphone transport' : 'start voice chat'} when ready.
        </p>
      )}
      <details className="voice-activity" hidden>
        <summary>Activity</summary>
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Keyboard users must be able to scroll the bounded activity region. */}
        <div role="region" tabIndex={0} aria-label="Voice activity">
          {activity.length ? (
            activity.map((label, index) => <p key={index}>{label}</p>)
          ) : (
            <p>No support activity yet.</p>
          )}
        </div>
      </details>
      <div className="voice-controls">
        <div className="voice-button-row">
          <button
            type="button"
            className="button button-secondary"
            disabled={!!socket.current || busy || !readiness?.text_ready}
            aria-busy={busy}
            onClick={() => void start(false)}
          >
            Start text support
          </button>
          {canReconnect && (
            <button
              type="button"
              className="button button-neutral"
              disabled={!readiness?.text_ready || busy}
              onClick={() => void start(false, true)}
            >
              Reconnect with text
            </button>
          )}
          {canReconnect && microphoneRequested && <button type="button" className="button button-secondary" disabled={!readiness?.voice_ready || busy} onClick={() => void start(true, true)}>Reconnect voice</button>}
          {(connected || busy) && <button
            type="button"
            className="button button-toggle"
            aria-pressed={muted}
            disabled={!connected || !microphoneActive}
            onClick={() => {
              if (audio.current) {
                audio.current.setMuted(!muted)
                setMuted(!muted)
                setInputLevel(0)
              }
            }}
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>}
          {(connected || busy) && <button
            type="button"
            className="button button-danger"
            disabled={!connected && !busy}
            onClick={() => void stop()}
          >
            Stop session
          </button>}
          {conversation && (
            <button
              type="button"
              className="button button-success"
              disabled={saving || busy}
              aria-busy={saving}
              onClick={() => void save()}
            >
              {saving ? 'Saving…' : 'Save case summary'}
            </button>
          )}
        </div>
        {explicitlyStopped.current && (
          <p className="readiness-note">
            This session has stopped. Start a new session; disconnected sessions can reconnect
            through text.
          </p>
        )}
        {conversation && <form className="voice-composer" onSubmit={submit}>
          <label htmlFor="voice-text">Message DeskMate</label>
          <div>
            <input
              id="voice-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Describe the issue or type a correction…"
              maxLength={8000}
              disabled={!connected}
            />
            <button className="button button-send" disabled={!connected || !text.trim()}>
              Send message
            </button>
          </div>
        </form>}
      </div>
    </section>
  )
}
