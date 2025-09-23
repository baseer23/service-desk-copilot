# MacBook Air Operator Runbook

This guide helps operators keep DeskMate responsive on a MacBook Air while
choosing the right model tier.

## Choosing a local model

- **Start with Phi 3 Mini.** It fits comfortably in memory and gives the best
  quality under the Air’s thermal limits.
- **Switch to TinyLlama when:**
  - the fan ramps or the chassis feels hot,
  - first-token time climbs above ~2 seconds, or
  - you see tokens-per-second visibly slowing during demos.
- Update `MODEL_NAME` in `.env` or use `templates/tier-a-mac-air.env`, then
  restart the backend so the helper can pick the new model.

## Keep answers tight

- Favour concise questions and answers to stay within the small context window.
- Use follow-ups instead of one long request.
- Rely on the citations UI and keep the chat thread focused on the current task.

## When to promote to hosted (Tier B)

- Long procedural walkthroughs where local models lose coherence.
- Demos for non-technical stakeholders who expect polished phrasing.
- Busy sessions with many concurrent chats or when local latency feels sluggish.
- Switch back to Tier A once the burst is over to save hosted quota.

## Guardrails and fallbacks

- If no local model is available the app uses the deterministic stub and the
  header pill calls this out. Pull `phi3:mini` or `tinyllama` and restart.
- When a hosted key is configured but unreachable the backend falls back to the
  stub answer and the health check warns that the hosted path is offline.

## Quick health checks

- Run `make bench-air` to capture local vs hosted latency and a one-line
  recommendation in `logs/mac-air-check.txt`.
- Use the header pill notes to confirm which tier is active before a demo.
