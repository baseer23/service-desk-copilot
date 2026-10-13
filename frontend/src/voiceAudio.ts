import { VoiceClientError } from './voiceErrors'
/** Raw PCM16 capture and bounded ordered playback; no recorded audio persistence. */
export class VoiceAudio {
  stream: MediaStream | null = null
  context: AudioContext | null = null
  capture: AudioWorkletNode | null = null
  input: MediaStreamAudioSourceNode | null = null
  gain: GainNode | null = null
  muted = false
  playback = new Set<AudioBufferSourceNode>()
  nextTime = 0
  closed = false
  generation = 0
  finishTimer: ReturnType<typeof setTimeout> | undefined

  async start(
    send: (audio: string) => void,
    progress: (stage: string) => void = () => undefined,
  ): Promise<void> {
    if (window.isSecureContext === false)
      throw new VoiceClientError(
        'MIC_INSECURE_CONTEXT',
        'Microphone access requires HTTPS or localhost. Use one localhost origin consistently.',
      )
    if (!navigator.mediaDevices?.getUserMedia)
      throw new VoiceClientError(
        'MIC_UNSUPPORTED',
        'Microphone access is unavailable in this browser. Use text support.',
      )
    progress('Requesting microphone permission')
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: false,
      })
    } catch (error) {
      const name = (error as { name?: string }).name
      if (name === 'NotAllowedError' || name === 'SecurityError')
        throw new VoiceClientError(
          'MIC_PERMISSION_DENIED',
          'Microphone permission was denied. Enable microphone access in your browser settings, or start text support.',
        )
      if (name === 'NotFoundError')
        throw new VoiceClientError(
          'MIC_NOT_FOUND',
          'No microphone was found. Connect a microphone or start text support.',
        )
      throw new VoiceClientError(
        'MIC_UNAVAILABLE',
        'The microphone could not be opened. Check whether another application is using it, or start text support.',
      )
    }
    if (this.closed) {
      this.stopCapture()
      return
    }
    progress('Opening microphone')
    try {
      this.context = new AudioContext({ sampleRate: 24000 })
    } catch {
      throw new VoiceClientError(
        'AUDIO_CONTEXT_UNAVAILABLE',
        'Browser audio could not start. Use text support.',
      )
    }
    if (this.context.sampleRate !== 24000)
      throw new VoiceClientError(
        'AUDIO_SAMPLE_RATE_UNSUPPORTED',
        'This browser cannot capture 24 kHz audio. Use text support.',
      )
    try {
      await this.context.audioWorklet.addModule('/pcm-worklet.js')
    } catch {
      throw new VoiceClientError(
        'AUDIO_PROCESSOR_LOAD_FAILED',
        'The audio processor could not be loaded. Reload the page or use text support.',
      )
    }
    if (this.closed) return
    try {
      this.input = this.context.createMediaStreamSource(this.stream)
      this.capture = new AudioWorkletNode(this.context, 'deskmate-pcm')
      this.capture.port.onmessage = (event) => {
        if (this.muted || this.closed) return
        const bytes = new Uint8Array(event.data)
        let value = ''
        for (const byte of bytes) value += String.fromCharCode(byte)
        send(btoa(value))
      }
      this.gain = this.context.createGain()
      this.gain.gain.value = 0
      this.input.connect(this.capture)
      this.capture.connect(this.gain)
      this.gain.connect(this.context.destination)
      let timer: ReturnType<typeof setTimeout> | undefined
      try {
        await Promise.race([
          this.context.resume(),
          new Promise<never>((_, reject) => {
            timer = setTimeout(() => reject(new Error('timeout')), 8000)
          }),
        ])
      } finally {
        clearTimeout(timer)
      }
      if (this.context.state === 'suspended' || this.context.state === 'closed')
        throw new Error('audio suspended')
    } catch {
      throw new VoiceClientError(
        'AUDIO_CAPTURE_FAILED',
        'Microphone audio could not start. Click again to enable browser audio, or use text support.',
      )
    }
  }

  async play(encoded: string): Promise<void> {
    const generation = this.generation
    if (this.closed) return
    if (!this.context) this.context = new AudioContext({ sampleRate: 24000 })
    await this.context.resume()
    if (this.closed || !this.context || generation !== this.generation) return
    const bytes = Uint8Array.from(atob(encoded), (value) => value.charCodeAt(0))
    if (!bytes.length || bytes.length % 2 || bytes.length > 49152)
      throw new Error('Invalid audio from the server')
    if (this.playback.size >= 128 || this.nextTime - this.context.currentTime > 15)
      throw new Error('Audio playback is too far behind. Please reconnect.')
    const buffer = this.context.createBuffer(1, bytes.length / 2, 24000)
    const view = new DataView(bytes.buffer)
    const samples = buffer.getChannelData(0)
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768
    const source = this.context.createBufferSource()
    source.buffer = buffer
    source.connect(this.context.destination)
    this.playback.add(source)
    source.onended = () => this.playback.delete(source)
    const start = Math.max(this.context.currentTime + 0.01, this.nextTime)
    source.start(start)
    this.nextTime = start + buffer.duration
  }

  clearPlayback(): void {
    this.generation++
    for (const source of this.playback) {
      source.stop()
      source.disconnect()
    }
    this.playback.clear()
    this.nextTime = 0
  }
  stopCapture(): void {
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    this.capture?.disconnect()
    this.input?.disconnect()
    this.gain?.disconnect()
  }
  finish(): void {
    this.stopCapture()
    const remaining = Math.max(0, this.nextTime - (this.context?.currentTime ?? 0)) * 1000
    this.finishTimer = setTimeout(() => this.close(), Math.min(16000, remaining + 100))
  }

  close(): void {
    clearTimeout(this.finishTimer)
    this.closed = true
    this.stopCapture()
    this.clearPlayback()
    void this.context?.close()
    this.context = null
  }
}
