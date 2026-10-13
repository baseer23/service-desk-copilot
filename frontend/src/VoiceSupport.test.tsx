import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import VoiceSupport, { Transcript } from './VoiceSupport'
import { VoiceAudio } from './voiceAudio'
import type { ReadinessReport } from './readiness'

const capability = {
  configured: true,
  locally_initialized: true,
  authenticated: 'NOT_CHECKED',
  reachable: 'NOT_CHECKED',
  live_validated: false,
  missing: [],
}
const azureReady: ReadinessReport = {
  runtime_mode: 'azure',
  reasoning: capability,
  embeddings: capability,
  voice: capability,
  local_mcp: 'READY',
  private_stores: 'READY',
  session_auth_configured: true,
  text_ready: true,
  voice_ready: true,
  live_azure_validated: false,
}
const sid = 'a'.repeat(32)
class Socket {
  static OPEN = 1
  static instances: Socket[] = []
  readyState = 1
  bufferedAmount = 0
  sent: string[] = []
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    Socket.instances.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close() {
    this.onclose?.()
  }
  emit(type: string, sequence: number, payload: unknown) {
    this.onmessage?.({
      data: JSON.stringify({ version: 1, session_id: sid, type, sequence, payload }),
    })
  }
}
beforeEach(() => {
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      json: async () => ({
        session_id: sid,
        status: 'listening',
        turns: [],
        case_id: null,
        resolution_status: 'unconfirmed',
      }),
    })),
  )
  vi.spyOn(VoiceAudio.prototype, 'play').mockResolvedValue()
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('no microphone before click; rejection offers text and cleans tracks', async () => {
  const getUserMedia = vi.fn().mockRejectedValue(new Error('permission denied'))
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  expect(getUserMedia).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Start voice chat' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('start text support')
  expect(getUserMedia).toHaveBeenCalledOnce()
  expect(fetch).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  expect(Socket.instances).toHaveLength(1)
})

test('text fallback, stable partial bubbles, duplicate ordering and interruption', async () => {
  const clear = vi.spyOn(VoiceAudio.prototype, 'clearPlayback')
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  expect(screen.getByRole('list', { name: 'Connection progress' })).not.toHaveTextContent(
    'microphone',
  )
  const ws = Socket.instances[0]
  act(() => {
    ws.emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    })
    ws.emit('transcript.user.partial', 2, { turn_id: 'u', text: 'VPN ' })
    ws.emit('transcript.user.partial', 3, { turn_id: 'u', text: 'broken' })
    ws.emit('transcript.user.final', 4, { turn_id: 'u', text: 'VPN broken' })
    ws.emit('transcript.user.final', 4, { turn_id: 'u', text: 'DUPLICATE' })
    ws.emit('audio', 5, { audio: 'AAA=' })
    ws.emit('playback.clear', 6, {})
  })
  expect(screen.getAllByTestId('voice-message')).toHaveLength(1)
  expect(screen.getByText('VPN broken')).toBeInTheDocument()
  expect(screen.queryByText('DUPLICATE')).not.toBeInTheDocument()
  expect(clear).toHaveBeenCalled()
  await userEvent.type(screen.getByLabelText('Message DeskMate'), 'it worked')
  await userEvent.click(screen.getByRole('button', { name: 'Send message' }))
  expect(JSON.parse(ws.sent[0]).type).toBe('text.input')
  act(() => ws.emit('pong', 9, {}))
  expect(screen.getByRole('alert')).toHaveTextContent('out of order')
})

test('streamed audio plays in order, stop/unmount closes capture and session', async () => {
  const played: string[] = []
  vi.spyOn(VoiceAudio.prototype, 'play').mockImplementation(async (value) => {
    played.push(value)
  })
  const close = vi.spyOn(VoiceAudio.prototype, 'close')
  const view = render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  const ws = Socket.instances[0]
  act(() => {
    ws.emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    })
    ws.emit('audio', 2, { audio: 'first' })
    ws.emit('audio', 3, { audio: 'second' })
  })
  await waitFor(() => expect(played).toEqual(['first', 'second']))
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  expect(close).toHaveBeenCalled()
  expect(JSON.parse(ws.sent[ws.sent.length - 1]).type).toBe('session.stop')
  view.unmount()
})

test('actual audio cleanup stops tracks even when permission completes after unmount', async () => {
  let resolve: (value: MediaStream) => void = () => undefined
  const stop = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: () =>
        new Promise<MediaStream>((r) => {
          resolve = r
        }),
    },
  })
  const player = new VoiceAudio()
  const pending = player.start(vi.fn())
  player.close()
  resolve({ getTracks: () => [{ stop }] } as unknown as MediaStream)
  await pending
  expect(stop).toHaveBeenCalledOnce()
})

test('1000 messages stay in one scroll panel; preserve upward scroll and jump', () => {
  const turns = Array.from({ length: 1000 }, (_, index) => ({
    id: String(index),
    role: index % 2 ? ('assistant' as const) : ('user' as const),
    text: 'message ' + index,
  }))
  const view = render(<Transcript turns={turns} />)
  const panel = screen.getByTestId('voice-transcript')
  Object.defineProperties(panel, {
    scrollHeight: { configurable: true, value: 100000 },
    clientHeight: { configurable: true, value: 480 },
  })
  panel.scrollTop = 10
  fireEvent.scroll(panel)
  view.rerender(
    <Transcript turns={[...turns, { id: 'latest', role: 'assistant', text: 'newest' }]} />,
  )
  expect(panel.scrollTop).toBe(10)
  expect(screen.getAllByTestId('voice-message')).toHaveLength(1001)
  fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }))
  expect(panel.scrollTop).toBe(100000)
  expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument()
  panel.scrollTop = 99530
  fireEvent.scroll(panel)
  view.rerender(
    <Transcript turns={[...turns, { id: 'newer', role: 'assistant', text: 'newer' }]} />,
  )
  expect(panel.scrollTop).toBe(100000)
})

test('successful synthetic capture mutes frames and stops tracks on session stop', async () => {
  const stop = vi.fn()
  const contextClose = vi.fn().mockResolvedValue(undefined)
  const node = { connect: vi.fn(), disconnect: vi.fn() }
  class Context {
    sampleRate = 24000
    audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) }
    destination = {}
    createMediaStreamSource = () => node
    createGain = () => ({ ...node, gain: { value: 1 } })
    resume = vi.fn().mockResolvedValue(undefined)
    close = contextClose
  }
  class Worklet {
    static last: Worklet
    port: { onmessage: ((event: { data: ArrayBuffer }) => void) | null } = { onmessage: null }
    connect = vi.fn()
    disconnect = vi.fn()
    constructor() {
      Worklet.last = this
    }
  }
  vi.stubGlobal('AudioContext', Context)
  vi.stubGlobal('AudioWorkletNode', Worklet)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }) },
  })
  const view = render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start voice chat' }))
  const ws = Socket.instances[0]
  act(() =>
    ws.emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    }),
  )
  await userEvent.click(screen.getByRole('button', { name: 'Mute' }))
  Worklet.last.port.onmessage?.({ data: new ArrayBuffer(960) })
  expect(ws.sent).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: 'Unmute' }))
  Worklet.last.port.onmessage?.({ data: new ArrayBuffer(960) })
  expect(JSON.parse(ws.sent[0]).type).toBe('audio.append')
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  expect(stop).toHaveBeenCalledOnce()
  expect(contextClose).toHaveBeenCalledOnce()
  view.unmount()
  expect(stop).toHaveBeenCalledOnce()
})

test('client queue and send buffer saturation close the session explicitly', async () => {
  vi.spyOn(VoiceAudio.prototype, 'play').mockImplementation(
    () => new Promise<void>(() => undefined),
  )
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  const ws = Socket.instances[0]
  act(() => {
    ws.emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    })
    for (let index = 0; index < 65; index++) ws.emit('audio', index + 2, { audio: 'AAA=' })
  })
  expect(screen.getByRole('alert')).toHaveTextContent('queue is full')
  expect(screen.getByTestId('voice-status')).toHaveTextContent('failed')
})

test('isolated demo has an explicit warning and a microphone plumbing button', () => {
  render(
    <VoiceSupport
      onCaseCreated={vi.fn()}
      readiness={{ ...azureReady, runtime_mode: 'isolated_demo' }}
    />,
  )
  expect(screen.getByText('ISOLATED DEMO')).toBeInTheDocument()
  expect(
    screen.getByText('Responses and audio are simulated. Azure is not being used.'),
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Test microphone' })).toHaveClass('button-primary')
  expect(screen.queryByRole('button', { name: 'Start voice chat' })).not.toBeInTheDocument()
  expect(screen.getByText(/will not recognize speech or speak/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeEnabled()
})

test('late playback failure from a stopped session cannot close the new session', async () => {
  let rejectPlayback: (error: Error) => void = () => undefined
  vi.spyOn(VoiceAudio.prototype, 'play').mockImplementation(
    () =>
      new Promise<void>((_resolve, reject) => {
        rejectPlayback = reject
      }),
  )
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  const old = Socket.instances[0]
  act(() => {
    old.emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    })
    old.emit('audio', 2, { audio: 'AAA=' })
  })
  await waitFor(() => expect(VoiceAudio.prototype.play).toHaveBeenCalled())
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  act(() =>
    Socket.instances[1].emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    }),
  )
  await act(async () => {
    rejectPlayback(new Error('private expired playback response'))
  })
  expect(screen.getByTestId('voice-status')).toHaveTextContent('listening')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('missing Azure readiness disables only the relevant controls', () => {
  const report = {
    ...azureReady,
    voice_ready: false,
    voice: { ...capability, configured: false, missing: ['AZURE_VOICELIVE_ENDPOINT'] },
  }
  const view = render(<VoiceSupport onCaseCreated={vi.fn()} readiness={report} />)
  expect(screen.getByRole('button', { name: 'Start voice chat' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeEnabled()
  expect(screen.getByText(/Voice unavailable: AZURE_VOICELIVE_ENDPOINT/)).toBeInTheDocument()
  view.rerender(
    <VoiceSupport
      onCaseCreated={vi.fn()}
      readiness={{
        ...report,
        text_ready: false,
        reasoning: { ...capability, configured: false, missing: ['AZURE_MODEL_DEPLOYMENT'] },
      }}
    />,
  )
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeDisabled()
  expect(screen.getByText(/Text support unavailable: AZURE_MODEL_DEPLOYMENT/)).toBeInTheDocument()
})

test('permission denial exposes stages and a safe error code, never the raw error', async () => {
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi
        .fn()
        .mockRejectedValue(new DOMException('private raw detail', 'NotAllowedError')),
    },
  })
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start voice chat' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Microphone permission was denied')
  expect(screen.getByRole('list', { name: 'Connection progress' })).toHaveTextContent(
    'Preparing sessionRequesting microphone permissionOpening microphoneConnectingConnectedListening',
  )
  await userEvent.click(screen.getByText('Error detail'))
  expect(screen.getByText('MIC_PERMISSION_DENIED')).toBeInTheDocument()
  expect(screen.queryByText('private raw detail')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeEnabled()
})

test('AudioWorklet load failure is visible and capture is cleaned up', async () => {
  const stop = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }) },
  })
  class Context {
    sampleRate = 24000
    audioWorklet = { addModule: vi.fn().mockRejectedValue(new Error('private processor response')) }
    close = vi.fn().mockResolvedValue(undefined)
  }
  vi.stubGlobal('AudioContext', Context)
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start voice chat' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('audio processor could not be loaded')
  expect(screen.getByText('AUDIO_PROCESSOR_LOAD_FAILED')).toBeInTheDocument()
  expect(stop).toHaveBeenCalledOnce()
  expect(screen.queryByText('private processor response')).not.toBeInTheDocument()
})

test('WebSocket failure is shown with a code and a stopped session cannot reconnect', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={azureReady} />)
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  act(() => Socket.instances[0].onerror?.())
  expect(screen.getByRole('alert')).toHaveTextContent('WebSocket could not connect')
  expect(screen.getByText('VOICE_WS_CONNECT_FAILED')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Start text support' }))
  const ws = Socket.instances[1]
  act(() =>
    ws.emit('session.snapshot', 1, {
      conversation: { session_id: sid, status: 'listening', turns: [] },
    }),
  )
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  expect(screen.queryByRole('button', { name: 'Reconnect with text' })).not.toBeInTheDocument()
  expect(screen.getByText(/This session has stopped/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeEnabled()
})

test('shows the remaining estimated CAD allowance without claiming an invoice cap', () => {
  render(
    <VoiceSupport
      onCaseCreated={vi.fn()}
      readiness={{
        ...azureReady,
        azure_usage: {
          enabled: true,
          remaining_estimated_cad: 2.75,
          estimated_used_cad: 5.25,
          limit_cad: 8,
          invoice_cap: false,
          ledger_error: false,
        },
      }}
    />,
  )
  const panel = screen.getByLabelText('Estimated Azure demo allowance')
  expect(panel).toHaveTextContent('CAD $2.75')
  expect(panel).toHaveTextContent('does not cap your Azure invoice')
})

test('emergency Azure disable closes transport and refreshes readiness', async () => {
  const usage = {
    enabled: true,
    remaining_estimated_cad: 8,
    estimated_used_cad: 0,
    limit_cad: 8,
    invoice_cap: false as const,
    ledger_error: false,
  }
  const stopped = {
    ...azureReady,
    text_ready: false,
    voice_ready: false,
    azure_usage: { ...usage, enabled: false },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => stopped })),
  )
  render(<VoiceSupport onCaseCreated={vi.fn()} readiness={{ ...azureReady, azure_usage: usage }} />)
  await userEvent.click(screen.getByRole('button', { name: 'Disable Azure usage' }))
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining('/azure/usage/disable'),
    expect.objectContaining({ method: 'POST' }),
  )
  await waitFor(() =>
    expect(screen.getByLabelText('Estimated Azure demo allowance')).toHaveTextContent('Disabled'),
  )
  expect(screen.getByRole('button', { name: 'Start voice chat' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeDisabled()
})

test('shows real validation failures and blocks live controls without a fake fallback', () => {
  render(
    <VoiceSupport
      onCaseCreated={vi.fn()}
      readiness={{
        ...azureReady,
        text_ready: false,
        voice_ready: false,
        azure_validation: { text_workflow: { passed: false, code: 'rate_limit_exceeded' } },
      }}
    />,
  )
  expect(screen.getByRole('alert')).toHaveTextContent('rate_limit_exceeded')
  expect(screen.getByRole('button', { name: 'Start voice chat' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Start text support' })).toBeDisabled()
  expect(screen.queryByText('ISOLATED DEMO')).not.toBeInTheDocument()
})
