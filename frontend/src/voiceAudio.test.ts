import worklet from '../public/pcm-worklet.js?raw'
import { afterEach, expect, test, vi } from 'vitest'
import { VoiceAudio } from './voiceAudio'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test.each([24000, 44100, 48000])('the real worklet resamples %i Hz across render blocks to PCM16LE', rate => {
  const messages: { audio: ArrayBuffer; level: number }[] = []
  let Processor: new () => { process: (inputs: Float32Array[][]) => boolean }
  const load = new Function('sampleRate', 'AudioWorkletProcessor', 'registerProcessor', worklet)
  load(rate,
    class { port = { postMessage: (value: { audio: ArrayBuffer; level: number }) => messages.push(value) } },
    (_name: string, value: typeof Processor) => { Processor = value },
  )
  const processor = new Processor!()
  for (let offset = 0; offset < rate * 2; offset += 128) {
    expect(processor.process([[new Float32Array(Math.min(128, rate * 2 - offset)).fill(0.25)]])).toBe(true)
  }
  expect(messages).toHaveLength(100)
  for (const message of messages) {
    expect(message.audio.byteLength).toBe(960)
    expect(message.level).toBeCloseTo(0.25)
    const view = new DataView(message.audio)
    expect(view.getInt16(0, true)).toBe(8192)
    expect(view.getInt16(958, true)).toBe(8192)
  }
})

test('physical-device selection, native sample-rate fallback, mute and track cleanup', async () => {
  const track = { readyState: 'live', enabled: true, stop: vi.fn() }
  const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [track], getAudioTracks: () => [track] })
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
  const node = { connect: vi.fn(), disconnect: vi.fn() }
  const close = vi.fn().mockResolvedValue(undefined)
  class Context {
    sampleRate = 48000
    state = 'running'
    destination = {}
    audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) }
    resume = vi.fn().mockResolvedValue(undefined)
    close = close
    createMediaStreamSource = () => node
    createGain = () => ({ ...node, gain: { value: 1 } })
  }
  class Worklet {
    static last: Worklet
    port: { onmessage: ((event: { data: { audio: ArrayBuffer; level: number } }) => void) | null } = { onmessage: null }
    connect = vi.fn()
    disconnect = vi.fn()
    constructor() { Worklet.last = this }
  }
  vi.stubGlobal('AudioContext', Context)
  vi.stubGlobal('AudioWorkletNode', Worklet)
  const send = vi.fn(), activity = vi.fn(), audio = new VoiceAudio()
  await audio.start(send, undefined, activity, 'chosen-physical-device')
  expect(getUserMedia).toHaveBeenCalledWith(expect.objectContaining({ audio: expect.objectContaining({ deviceId: { exact: 'chosen-physical-device' }, channelCount: 1 }) }))
  expect(audio.capturing).toBe(true)
  Worklet.last.port.onmessage?.({ data: { audio: new ArrayBuffer(960), level: 0.15 } })
  expect(send).toHaveBeenCalledOnce()
  expect(activity).toHaveBeenLastCalledWith(0.15)
  audio.setMuted(true)
  expect(track.enabled).toBe(false)
  Worklet.last.port.onmessage?.({ data: { audio: new ArrayBuffer(960), level: 0.15 } })
  expect(send).toHaveBeenCalledTimes(2)
  expect(atob(send.mock.calls[1][0])).toBe('\0'.repeat(960))
  expect(activity).toHaveBeenLastCalledWith(0)
  audio.setMuted(false)
  expect(track.enabled).toBe(true)
  audio.close()
  expect(track.stop).toHaveBeenCalledOnce()
  expect(close).toHaveBeenCalledOnce()
  expect(audio.capturing).toBe(false)
})
