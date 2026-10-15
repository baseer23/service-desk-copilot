// Resample the context's actual rate into mono 16 kHz, 20 ms PCM16LE.
// A weighted average preserves phase across render blocks and filters downsampling.
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.frame = new Int16Array(320)
    this.offset = 0
    this.ratio = sampleRate / 16000
    this.remaining = this.ratio
    this.sum = 0
  }
  process(inputs) {
    const samples = inputs[0]?.[0]
    if (samples)
      for (const input of samples) {
        let available = 1
        while (available > 1e-8) {
          const weight = Math.min(available, this.remaining)
          this.sum += input * weight
          this.remaining -= weight
          available -= weight
          if (this.remaining > 1e-8) continue
          const sample = Math.max(-1, Math.min(1, this.sum / this.ratio))
          this.frame[this.offset++] = Math.round(sample * (sample < 0 ? 32768 : 32767))
          this.remaining = this.ratio
          this.sum = 0
          if (this.offset !== 320) continue
          const bytes = new ArrayBuffer(640)
          const view = new DataView(bytes)
          let energy = 0
          for (let i = 0; i < 320; i++) {
            view.setInt16(i * 2, this.frame[i], true)
            energy += (this.frame[i] / 32768) ** 2
          }
          this.port.postMessage({ audio: bytes, level: Math.sqrt(energy / 320) }, [bytes])
          this.offset = 0
        }
      }
    return true
  }
}
registerProcessor('deskmate-pcm', PCMProcessor)
