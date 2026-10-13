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
  const [errorCode, setErrorCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [microphoneActive, setMicrophoneActive] = useState(false)
  const [microphoneRequested, setMicrophoneRequested] = useState(false)
  const attempt = useRef(0)
  const explicitlyStopped = useRef(false)
  const connectionTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const readyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
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
    playbackEpoch.current++
    audioQueue.current = { pending: 0 }
    playback.current = Promise.resolve()
    streamReady.current = false
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

  function fail(error: unknown) {
    const visible = visibleVoiceError(error)
    setError(visible.message)
    setErrorCode(visible.code)
    cleanup()
    setMicrophoneActive(false)
    setStatus('failed')
  }
  function send(type: string, payload: Record<string, unknown> = {}) {
    const ws = socket.current
    if (type === 'audio.append' && !streamReady.current) return
    if (ws?.readyState !== WebSocket.OPEN) return
    if (ws.bufferedAmount > 262144) {
      fail(
        new VoiceClientError(
          'AUDIO_CONNECTION_SLOW',
          'Audio connection is too slow. Reconnect or use text.',
        ),
      )
      return
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
          setStage(terminal.has(state.status) ? '' : 'Listening')
          setConversation(state)
          setTurns(state.turns)
          setStatus(state.status)
          if (terminal.has(state.status)) {
            audio.current?.stopCapture()
            setMicrophoneActive(false)
          }
        } else if (message.type === 'status') setStatus(String(p.status))
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
    setErrorCode('')
    setStatus('connecting')
    setMicrophoneRequested(withMicrophone)
    setStage('Preparing session')
    setMuted(false)
    audio.current?.close()
    audio.current = new VoiceAudio()
    try {
      if (withMicrophone) {
        await audio.current.start(
          (data) => send('audio.append', { audio: data }),
          (next) => {
            if (attempt.current === currentAttempt && mounted.current) setStage(next)
          },
        )
        setMicrophoneActive(!!audio.current?.stream)
      }
      if (!mounted.current || attempt.current !== currentAttempt || !starting.current) return
      setStage('Connecting')
      const state = await request<Conversation>(
        '/voice/sessions',
        post(
          resume && conversation
            ? { resume_session_id: conversation.session_id, mode: 'text' }
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
    if (id)
      try {
        const state = await request<Conversation>(`/voice/sessions/${id}/stop`, post())
        setConversation(state)
      } catch {
        setError('Connection closed. The server will also expire the session automatically.')
        setErrorCode('VOICE_STOP_REQUEST_FAILED')
      }
  }
  async function disableAzure() {
    try {
      await request('/azure/usage/disable', post())
      window.dispatchEvent(new Event('azure-usage-disabled'))
      await stop()
      await refresh()
    } catch (error) {
      fail(error)
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
      setErrorCode('VOICE_CASE_SAVE_FAILED')
    } finally {
      setSaving(false)
    }
  }
  const connected = !terminal.has(status) && status !== 'idle' && status !== 'connecting'
  const busy = status === 'connecting'
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
  const missingVoice = [
    ...(readiness?.reasoning.missing ?? []),
    ...(readiness?.voice.missing ?? []),
    ...(readiness?.embeddings.missing ?? []),
  ]
  return (
    <section className="voice-support card" aria-label="Voice Support">
      <header className="voice-heading">
        <div>
          <span className="eyebrow">TALK TO DESKMATE</span>
          <h2>Voice Support</h2>
        </div>
        <span className={`provider-badge ${demo ? 'demo' : ''}`}>
          {demo ? 'Isolated demo' : 'Azure'} {readiness ? '' : 'readiness pending'}
        </span>
      </header>
      <p>Investigate an IT issue through voice or text, with a visible transcript and sources.</p>
      {demo && (
        <div className="demo-banner">
          <strong>ISOLATED DEMO</strong>
          <p>Responses and audio are simulated. Azure is not being used.</p>
          <p>
            Use text support. Test microphone checks capture and transport only; it will not
            recognize speech or speak.
          </p>
        </div>
      )}
      <div className="readiness-summary" aria-label="Support readiness">
        <span>
          Reasoning:{' '}
          {demo
            ? 'Simulated'
            : readiness?.reasoning.configured
              ? 'Configured'
              : 'Missing configuration'}
        </span>
        <span>
          Voice:{' '}
          {demo
            ? 'Simulated'
            : readiness?.voice.configured
              ? 'Configured'
              : 'Missing configuration'}
        </span>
        <span>Live validated: {readiness?.live_azure_validated ? 'Yes' : 'No'}</span>
        <button
          type="button"
          className="button button-subtle"
          disabled={checking || busy}
          aria-busy={checking}
          onClick={() => void refresh()}
        >
          {checking ? 'Checking status…' : 'Refresh status'}
        </button>
      </div>
      {!readiness && (
        <p className="readiness-note">
          Readiness could not be confirmed. Check the backend, then refresh status.
        </p>
      )}
      {!demo && readiness?.azure_usage && (
        <div className="readiness-note" aria-label="Estimated Azure demo allowance">
          <p>
            Azure usage: {readiness.azure_usage.enabled ? 'Enabled' : 'Disabled'}. Remaining
            estimated demo allowance: CAD $
            {readiness.azure_usage.remaining_estimated_cad.toFixed(2)} of CAD $
            {readiness.azure_usage.limit_cad.toFixed(2)} this month.
          </p>
          <p>This local estimate does not cap your Azure invoice or cover other resources.</p>
          <button type="button" className="button" onClick={() => void disableAzure()}>
            Disable Azure usage
          </button>
        </div>
      )}
      {!demo &&
        readiness?.azure_validation &&
        Object.entries(readiness.azure_validation)
          .filter(([, check]) => !check.passed)
          .map(([name, check]) => (
            <p role="alert" className="readiness-note" key={name}>
              Azure {name.replace(/_/g, ' ')} validation failed: {check.code || 'CHECK_FAILED'}.
              Live retries are stopped. Review AZURE_ACTIVATION_REVIEW.md before testing.
            </p>
          ))}
      {readiness && !readiness.voice_ready && (
        <p className="readiness-note">
          Voice unavailable:{' '}
          {missingVoice.length
            ? [...new Set(missingVoice)].join(', ')
            : 'Check local MCP, stores and session identity.'}
        </p>
      )}
      {readiness && !readiness.text_ready && (
        <p className="readiness-note">
          Text support unavailable:{' '}
          {readiness.reasoning.missing.join(', ') ||
            'Check local initialization and session identity.'}
        </p>
      )}
      <div className="voice-state">
        <span className={`status-dot ${connected ? 'active' : ''}`} aria-hidden="true" />
        <p role="status" data-testid="voice-status">
          {status.replace(/_/g, ' ')}
        </p>
        {microphoneActive && <span>Microphone {muted ? 'muted' : 'on'}</span>}
      </div>
      {stage && (
        <div className="connection-progress">
          <p aria-live="polite">{stage}</p>
          <ol aria-label="Connection progress">
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
          {errorCode && (
            <details>
              <summary>Error detail</summary>
              <code>{errorCode}</code>
            </details>
          )}
        </div>
      )}
      <Transcript turns={turns} />
      {!turns.length && (
        <p className="voice-empty">
          Your conversation will appear above. Start text support, or{' '}
          {demo ? 'test the microphone transport' : 'start voice chat'} when ready.
        </p>
      )}
      <details className="voice-activity">
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
            className="button button-primary"
            disabled={!!socket.current || busy || !readiness?.voice_ready}
            aria-busy={busy}
            onClick={() => void start(true)}
          >
            {demo ? 'Test microphone' : 'Start voice chat'}
          </button>
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
          <button
            type="button"
            className="button button-toggle"
            aria-pressed={muted}
            disabled={!connected || !microphoneActive}
            onClick={() => {
              if (audio.current) {
                audio.current.muted = !muted
                setMuted(!muted)
              }
            }}
          >
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button
            type="button"
            className="button button-danger"
            disabled={!connected && !busy}
            onClick={() => void stop()}
          >
            Stop session
          </button>
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
        <form className="voice-composer" onSubmit={submit}>
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
        </form>
      </div>
    </section>
  )
}
