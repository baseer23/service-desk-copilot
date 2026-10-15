import { VoiceClientError } from './voiceErrors'
/** Microphone PCM16LE capture for Azure Speech STT; no playback or audio persistence. */
export class VoiceAudio {
  stream: MediaStream | null = null
  context: AudioContext | null = null
  capture: AudioWorkletNode | null = null
  input: MediaStreamAudioSourceNode | null = null
  gain: GainNode | null = null
  muted = false
  closed = false
  frames = 0

  get capturing(): boolean {
    return (
      !this.closed &&
      this.context?.state === 'running' &&
      !!this.stream?.getAudioTracks().some((track) => track.readyState === 'live')
    )
  }

  setMuted(value: boolean): void {
    this.muted = value
    this.stream?.getAudioTracks().forEach((track) => {
      track.enabled = !value
    })
  }

  async start(
    send: (audio: ArrayBuffer) => void,
    progress: (stage: string) => void = () => undefined,
    activity: (level: number) => void = () => undefined,
    deviceId?: string,
    ended: () => void = () => undefined,
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
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        },
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
    this.stream.getAudioTracks().forEach((track) => {
      track.addEventListener?.(
        'ended',
        () => {
          if (!this.closed) ended()
        },
        { once: true },
      )
    })
    progress('Opening microphone')
    try {
      this.context = new AudioContext({ sampleRate: 16000 })
    } catch {
      try {
        this.context = new AudioContext()
      } catch {
        throw new VoiceClientError(
          'AUDIO_CONTEXT_UNAVAILABLE',
          'Browser audio could not start. Use text support.',
        )
      }
    }
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
      this.capture = new AudioWorkletNode(this.context, 'deskmate-pcm', {
        channelCount: 1,
        channelCountMode: 'explicit',
        channelInterpretation: 'speakers',
      })
      this.capture.port.onmessage = (event) => {
        this.frames++
        activity(this.muted || this.closed ? 0 : Number(event.data.level ?? 0))
        if (this.closed) return
        // Keep the PCM clock moving while muted, so server VAD can finish a
        // sentence. Never forward captured samples from a muted microphone.
        const bytes = this.muted
          ? new ArrayBuffer(640)
          : ((event.data.audio ?? event.data) as ArrayBuffer)
        send(bytes)
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

  stopCapture(): void {
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    if (this.capture) this.capture.port.onmessage = null
    this.capture?.disconnect()
    this.input?.disconnect()
    this.gain?.disconnect()
  }
  close(): void {
    this.closed = true
    this.stopCapture()
    void this.context?.close()
    this.context = null
  }
}
