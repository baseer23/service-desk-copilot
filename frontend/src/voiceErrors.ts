export class VoiceClientError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message)
  }
}
export function visibleVoiceError(error: unknown): VoiceClientError {
  if (error instanceof VoiceClientError) return error
  const code = (error as { code?: string })?.code
  if (code === 'VOICE_SESSION_TERMINAL')
    return new VoiceClientError(
      code,
      'This session has stopped. Start a new session or reconnect a disconnected session through text.',
    )
  if (code === 'AZURE_REASONING_NOT_READY')
    return new VoiceClientError(
      code,
      'Azure reasoning is not configured. Review the readiness checklist.',
    )
  if (code === 'AZURE_VOICE_NOT_READY')
    return new VoiceClientError(
      code,
      'Azure Voice Live is disabled or not configured. Review the readiness checklist.',
    )
  return new VoiceClientError(
    'VOICE_START_FAILED',
    'The support session could not start. Refresh status and try again, or use text support.',
  )
}
