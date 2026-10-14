export class VoiceClientError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message)
  }
}
export function visibleVoiceError(error: unknown): VoiceClientError {
  if (error instanceof VoiceClientError) return new VoiceClientError(error.code, publicErrorMessage(error.message))
  const code = (error as { code?: string })?.code
  if (code === 'VOICE_SESSION_TERMINAL')
    return new VoiceClientError(
      code,
      'This session has stopped. Start a new session or reconnect a disconnected session through text.',
    )
  if (code === 'AZURE_REASONING_NOT_READY')
    return new VoiceClientError(
      code,
      'Text support is temporarily unavailable. You can save your request and return later.',
    )
  if (code === 'AZURE_VOICE_NOT_READY')
    return new VoiceClientError(
      code,
      'Voice support is temporarily unavailable. You can save your request and return later.',
    )
  return new VoiceClientError(
    'VOICE_START_FAILED',
    'The support session could not start. Refresh status and try again, or use text support.',
  )
}
import { publicErrorMessage } from './api'
