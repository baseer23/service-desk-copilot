// AudioContext runs at Azure's documented 24 kHz rate. Emit mono 20 ms PCM16LE.
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.frame = new Int16Array(480)
    this.offset = 0
  }
  process(inputs) {
    const samples = inputs[0]?.[0]
    if (samples)
      for (const sample of samples) {
        this.frame[this.offset++] = Math.round(
          Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767),
        )
        if (this.offset === 480) {
          const bytes = new ArrayBuffer(960)
          const view = new DataView(bytes)
          for (let i = 0; i < 480; i++) view.setInt16(i * 2, this.frame[i], true)
          this.port.postMessage(bytes, [bytes])
          this.offset = 0
        }
      }
    return true
  }
}
registerProcessor('deskmate-pcm', PCMProcessor)
