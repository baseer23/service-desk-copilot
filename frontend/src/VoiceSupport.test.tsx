import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import VoiceSupport, { Transcript } from './VoiceSupport'
import { VoiceAudio } from './voiceAudio'

class Socket {
  static OPEN = 1
  static instances: Socket[] = []
  readyState = 1
  bufferedAmount = 0
  sent: (string | ArrayBuffer)[] = []
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  acknowledgeStop = true
  finalOnStop = ''
  constructor(public url: string) {
    Socket.instances.push(this)
  }
  send(data: string | ArrayBuffer) {
    this.sent.push(data)
    if (typeof data === 'string' && JSON.parse(data).type === 'stop' && this.acknowledgeStop) {
      if (this.finalOnStop) this.emit('transcript.final', { id: 'final-on-stop', text: this.finalOnStop })
      this.emit('state', { state: 'stopped' })
    }
  }
  close() {
    this.closed = true
    this.onclose?.({ code: 1000 })
  }
  emit(type: string, payload: object = {}) {
    this.onmessage?.({ data: JSON.stringify({ type, ...payload }) })
  }
}
let captureFrame: (frame: ArrayBuffer) => void
beforeEach(() => {
  Socket.instances = []
  vi.stubGlobal('WebSocket', Socket)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path === '/speech/readiness'
          ? { ready: true }
          : path === '/voice-conversation/start'
            ? { session_id: 'voice1', response: 'Hello, welcome to Digital nGenuity support. How can I help you today?' }
          : {
              response: 'I can help troubleshoot that. What error do you see?',
            },
    })),
  )
  vi.spyOn(VoiceAudio.prototype, 'start').mockImplementation(async function (send) {
    captureFrame = send
  })
  vi.spyOn(VoiceAudio.prototype, 'close').mockImplementation(() => undefined)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function start() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: 'Ready to Talk' }))
  const ws = Socket.instances[Socket.instances.length - 1]
  act(() => ws.emit('state', { state: 'listening' }))
  return ws
}

test('no microphone before click; continuous partial/final speech replaces partials and deduplicates final callbacks', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  expect(VoiceAudio.prototype.start).not.toHaveBeenCalled()
  const ws = await start()
  expect(String(ws.url)).toMatch(/\/speech\/stream$/)
  act(() => {
    ws.emit('transcript.partial', { id: '1:a', text: 'VPN' })
    ws.emit('transcript.partial', { id: '1:a', text: 'VPN fails' })
  })
  expect(screen.getAllByTestId('voice-message')).toHaveLength(2)
  expect(screen.getByText('VPN fails')).toBeInTheDocument()
  act(() => {
    ws.emit('transcript.final', { id: '1:a', text: 'VPN fails.' })
    ws.emit('transcript.final', { id: '1:a', text: 'VPN fails.' })
    ws.emit('transcript.final', { id: '1:b', text: 'I can sign in elsewhere.' })
  })
  expect(screen.getAllByTestId('voice-message')).toHaveLength(1)
  expect(screen.getByLabelText('Message DeskMate')).toHaveValue(
    'VPN fails. I can sign in elsewhere.',
  )
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Listening')
  expect(VoiceAudio.prototype.close).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
})

test('standalone spoken Stop is excluded; a sentence containing stop is preserved; Stop releases microphone and sends only text to email correction', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  const ws = await start()
  act(() => {
    ws.emit('transcript.final', { id: '1:a', text: 'The service will stop responding.' })
    ws.emit('transcript.final', { id: '1:b', text: 'Stop.' })
  })
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Processing')
  expect(screen.queryByText('Stop.')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Message DeskMate')).toHaveValue('The service will stop responding.')
  expect(VoiceAudio.prototype.close).toHaveBeenCalledOnce()
  expect(ws.sent).toContain(JSON.stringify({ type: 'stop' }))
  await screen.findByText('I can help troubleshoot that. What error do you see?')
  expect(fetch).toHaveBeenCalledWith(
    '/voice-conversation/voice1',
    expect.objectContaining({
      body: expect.stringContaining('"summary":"The service will stop responding."'),
    }),
  )
  expect(
    await screen.findByText('I can help troubleshoot that. What error do you see?'),
  ).toBeInTheDocument()
})

test('SDK reconnect state preserves accumulated transcript and keeps microphone; genuine usage includes silence', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  const ws = await start()
  act(() => {
    ws.emit('transcript.final', { id: '1:a', text: 'First words.' })
    ws.emit('state', { state: 'reconnecting' })
    ws.emit('usage', { listening_seconds: 120, connected_seconds: 115.4 })
  })
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Reconnecting')
  expect(screen.getByLabelText('Message DeskMate')).toHaveValue('First words.')
  expect(screen.getByLabelText('Speech usage')).toHaveTextContent(
    'Connected speech 115s',
  )
  expect(VoiceAudio.prototype.close).not.toHaveBeenCalled()
  act(() => {
    ws.emit('state', { state: 'listening' })
    ws.emit('transcript.final', { id: '2:b', text: 'Still listening.' })
  })
  expect(screen.getByLabelText('Message DeskMate')).toHaveValue('First words. Still listening.')
})

test('captures binary 16k PCM, handles mute, explicitly stops, permits next recording, and ignores stale callbacks', async () => {
  const view = render(<VoiceSupport onCaseCreated={vi.fn()} />)
  const old = await start()
  const frame = new ArrayBuffer(640)
  captureFrame(frame)
  expect(old.sent[0]).toBe(frame)
  await userEvent.click(screen.getByRole('button', { name: 'Mute' }))
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Microphone muted')
  await userEvent.click(screen.getByRole('button', { name: 'Unmute' }))
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  const next = await start()
  act(() => old.emit('error', { message: 'stale failure' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Listening')
  view.unmount()
  expect(next.closed).toBe(true)
  expect(VoiceAudio.prototype.close).toHaveBeenCalledTimes(2)
})

test('permission failure leaves text available and never opens speech WebSocket', async () => {
  vi.mocked(VoiceAudio.prototype.start).mockRejectedValue(
    new Error('Microphone permission was denied.'),
  )
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: 'Ready to Talk' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('permission was denied')
  expect(Socket.instances).toHaveLength(0)
  expect(screen.getByLabelText('Message DeskMate')).toBeEnabled()
})

test('server recognition faults are errors, release capture and preserve transcript', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  const ws = await start()
  act(() => {
    ws.emit('transcript.final', { id: '1:a', text: 'Preserve this.' })
    ws.emit('error', { message: 'Speech recognition stopped. Please try again.' })
  })
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Error')
  expect(screen.getByLabelText('Message DeskMate')).toHaveValue('Preserve this.')
  expect(VoiceAudio.prototype.close).toHaveBeenCalledOnce()
})

test('1000 messages remain in one bounded transcript panel with a jump control', () => {
  const turns = Array.from({ length: 1000 }, (_, i) => ({
    id: String(i),
    role: 'user' as const,
    text: 'message ' + i,
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
    <Transcript turns={[...turns, { id: 'latest', role: 'assistant', text: 'latest' }]} />,
  )
  expect(screen.getAllByTestId('voice-message')).toHaveLength(1001)
  fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }))
  expect(panel.scrollTop).toBe(100000)
})

test('Stop releases microphone immediately but the next recording waits for confirmed SDK settlement', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  const ws = await start()
  ws.acknowledgeStop = false
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  expect(VoiceAudio.prototype.close).toHaveBeenCalledOnce()
  expect(screen.getByTestId('voice-status')).toHaveTextContent('Processing')
  expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeDisabled()
  act(() => ws.emit('state', { state: 'stopped' }))
  expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeEnabled()
})

test('a final transcript flushed by Azure during stop is submitted exactly once', async () => {
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  const ws = await start()
  ws.finalOnStop = 'My printer is offline.'
  await userEvent.click(screen.getByRole('button', { name: 'Stop session' }))
  expect(await screen.findByText('My printer is offline.')).toBeInTheDocument()
  expect(await screen.findByText('I can help troubleshoot that. What error do you see?')).toBeInTheDocument()
  const calls = vi.mocked(fetch).mock.calls.filter(([path]) => path === '/voice-conversation/voice1')
  expect(calls).toHaveLength(1)
  expect(calls[0]?.[1]).toEqual(expect.objectContaining({
    body: expect.stringContaining('"summary":"My printer is offline."'),
  }))
})

test('new microphone capture waits while the submitted transcript investigation is in flight', async () => {
  let finish: (response: Response) => void = () => undefined
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) =>
      path === '/speech/readiness'
        ? Response.json({ ready: true })
        : path === '/voice-conversation/start'
          ? Response.json({ session_id: 'voice1', response: 'Hello, welcome to Digital nGenuity support. How can I help you today?' })
        : new Promise<Response>((resolve) => {
            finish = resolve
          }),
    ),
  )
  render(<VoiceSupport onCaseCreated={vi.fn()} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeEnabled())
  await userEvent.type(screen.getByLabelText('Message DeskMate'), 'VPN fails')
  await userEvent.click(screen.getByRole('button', { name: 'Send message' }))
  expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Start voice chat' })).toBeDisabled()
  expect(VoiceAudio.prototype.start).not.toHaveBeenCalled()
  await act(async () =>
    finish(
      Response.json({
        response: 'Verified text response',
      }),
    ),
  )
  expect(await screen.findByText('Verified text response')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Ready to Talk' })).toBeEnabled()
})
