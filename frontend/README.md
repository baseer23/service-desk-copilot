# DeskMate frontend

React/Vite supports existing text investigations and browser Voice Support.
`npm run dev` proxies HTTP and WebSocket routes to FastAPI; `API_PROXY_TARGET` may
change that server-side development target. Deploy the built UI and API under the
same HTTPS origin. No permanent Azure key belongs in a VITE environment variable.

Voice capture requires an explicit click, browser permission and a secure context
(HTTPS or localhost). It uses a 24 kHz AudioWorklet with mono PCM16, bounded ordered
playback, mute, interruption, stop, reconnect through text and text fallback.
The transcript scrolls within a fixed panel. Activity contains operational labels.

```bash
npm test
npm run build
npm run test:e2e
```

See [voice setup](../docs/VOICE.md). Browser tests use synthetic/fake services and
never access a real microphone or Azure. Voice-specific unit tests cover cleanup,
permission rejection, playback order, interruption and 1,000-message scrolling.

`/readiness` gates support controls by capability; configuration is distinct from
Azure authentication/reachability. The isolated demo has a prominent badge and
**Test microphone**, which does not recognize or speak. Capture shows permission,
audio initialization and connection stages, with safe error codes. Azure text
support uses structured reasoning without a Voice Live connection.

Timeline, Sources, Voice transcript and Activity scroll independently. Source
previews are limited to 600 characters; **Show full evidence** preserves access to
the original content. Buttons have bordered/filled variants, busy/disabled states
and focus rings. See [Azure portal setup](../docs/AZURE_LIVE_SETUP.md) and
[runtime review](../docs/READINESS_REVIEW.md).
