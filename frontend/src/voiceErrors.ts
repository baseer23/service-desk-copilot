export class VoiceClientError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message)
  }
}
