> **Archived — superseded on 2026-10-14.** This document records an earlier deployment, migration or validation. Its Azure persistence, Voice Live, billing and startup instructions are not current. Do not execute its old provisioning or deployment commands. See [current architecture](ARCHITECTURE_SIMPLIFICATION.md), [setup](../README.md) and [Speech](SPEECH.md). Historical backups and financial records remain protected.

# First local Azure acceptance

Prepared 2026-10-12. No Azure resources, cloud request or real microphone session
was created by this task. Azure CLI was absent on this machine. Complete the
non-secret decisions in [READINESS_REVIEW.md](READINESS_REVIEW.md) first.

The application keeps Voice Live model mode, application-owned LangGraph and local
stdio MCP. Select a Voice Live-capable Microsoft Foundry resource (`AIServices`,
S0), plus an Azure OpenAI-compatible resource exposing both reasoning and embedding
deployments. One resource may provide both capabilities if it exposes both required
origins; existing separate resources work. Choose the resource region deliberately:
`gpt-realtime` uses global inference, which is different from the resource location.
[Microsoft's resource guide](https://learn.microsoft.com/en-us/azure/ai-services/multi-service-resource)
and [Voice Live model/region guide](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live)
describe these resource and processing boundaries.

## Endpoints and exact settings

**`AZURE_FOUNDRY_ENDPOINT` means the Azure OpenAI resource origin**:
`https://RESOURCE.openai.azure.com`. The backend passes it as `azure_endpoint` to
`openai.AzureOpenAI`; that SDK adds `/openai/deployments/...`. Never enter a project
URL, `/api/projects/...`, `/openai/v1`, or the Voice Live URL in this setting. The
historical variable name is retained for compatibility. The
[2024-10-21 REST specification](https://raw.githubusercontent.com/Azure/azure-rest-api-specs/63d530d0def1c624f5d42d39170ff4ac196522e2/specification/cognitiveservices/data-plane/AzureOpenAI/inference/stable/2024-10-21/inference.json)
defines deployment chat completions, `json_schema`, and embedding `dimensions`.

`AZURE_VOICELIVE_ENDPOINT` is the HTTPS **resource origin**, typically
`https://RESOURCE.services.ai.azure.com`; older resource custom domains can use
`.cognitiveservices.azure.com`. The Voice Live SDK constructs WSS and its realtime
path. Keep `azure-ai-voicelive==1.3.0` and `2026-04-10`. The adapter uses
`gpt-4o-transcribe` with `gpt-realtime`, and the standard voice `en-US-AvaNeural`.
These are built-in Voice Live selections; no separate transcription deployment
is needed. See the [stable transcription contract](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10).

Enter the following locally, replacing placeholders in your editor. Start with
`VOICE_ENABLED=false`; enable it only at step 17. Do not copy over an existing `.env`.

```dotenv
TEST_MODE=false
DEPLOYMENT_MODE=local
GENERATION_PROVIDER=azure_openai
EMBED_PROVIDER=azure_openai
AZURE_USE_ENTRA_ID=true
AZURE_FOUNDRY_ENDPOINT=https://RESOURCE.openai.azure.com
AZURE_MODEL_DEPLOYMENT=deskmate-reasoning
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_STRUCTURED_OUTPUTS=true
AZURE_EMBEDDING_DEPLOYMENT=deskmate-embedding
EMBED_MODEL_NAME=text-embedding-3-small
AZURE_EMBEDDING_DIMENSIONS=384
MODEL_TIMEOUT_SEC=30
VOICE_ENABLED=false
VOICE_PROVIDER=azure_voicelive
AZURE_VOICELIVE_ENDPOINT=https://RESOURCE.services.ai.azure.com
AZURE_VOICELIVE_MODEL=gpt-realtime
AZURE_VOICELIVE_API_VERSION=2026-04-10
AZURE_VOICELIVE_VOICE=en-US-AvaNeural
VOICE_AUTH_MODE=local_demo
VOICE_TENANT_ID=live-acceptance
VOICE_SESSION_SIGNING_KEY=<copy-local-random-secret-in-your-editor>
ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
KNOWLEDGE_SCOPE=support
RESEARCH_SOURCE_POLICY=prefer_primary
NEO4J_URI=
CASE_DB_PATH=<new-absolute-private-directory>/cases.db
CHROMA_DIR=<same-absolute-directory>/chroma
```

For the explicit **local API-key alternative**, change `AZURE_USE_ENTRA_ID=false`
and enter `AZURE_OPENAI_API_KEY` and `AZURE_VOICELIVE_API_KEY` directly in the private
editor. They may be the same only when both capabilities belong to the same
resource. Keys never go in `VITE_*`, URLs, commands, screenshots or browser code.
The first loopback test uses local browser identity, independently of Azure Entra
service authentication. Production requires Entra browser sign-in, additionally
`VOICE_AUTH_TENANT_ID` and `VOICE_AUTH_AUDIENCE`, and a stable signing secret; the
standalone demo UI has no deployed sign-in flow yet.

## Portal and local procedure

These are operator instructions; resource creation, role assignment, sign-in and
live checks were not performed. Portal navigation below names the current Azure
portal and Foundry classic paths. A portal workspace may exist already; the app
does not require its project endpoint or any hosted agent.

1. **Select/create Voice Live resource.** In [Azure portal](https://portal.azure.com),
   open an existing Microsoft Foundry resource. Otherwise select **Create a resource
   → Foundry → Create**, choose subscription, resource group, region and name, then
   **Review + create → Create → Go to resource**. Confirm kind `AIServices` and S0.
   [Official creation steps](https://learn.microsoft.com/en-us/azure/ai-services/multi-service-resource).
2. **Region and quota.** Check the resource **Overview → Location** against the
   [Voice Live region matrix](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions#voice-live).
   Confirm the selected model is available to your subscription. Inspect resource
   **Usage + quotas** where available; if Voice Live limits are not displayed, ask
   Azure support to confirm capacity before acceptance. Do not assume OpenAI text
   quota proves Voice Live access. [Voice Live limits/support procedure](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits#voice-live-quotas-and-limits-per-resource).
3. **Voice endpoint.** Open the resource **Keys and Endpoint** and copy its custom
   HTTPS resource origin into the private editor as `AZURE_VOICELIVE_ENDPOINT`.
   Ignore project URLs and never share that page's keys.
4. **Select model without starting audio.** In [Foundry](https://ai.azure.com), with
   **New Foundry off**, select the resource/workspace, then **Playgrounds → Speech
   playground → Try the Speech playground → Speech capabilities by scenario → Voice
   Live → Configuration → GenAI → Generative AI model → gpt-realtime**. Do not press
   **Start** while inspecting availability. Set the application's model to
   `gpt-realtime`; Voice Live manages it rather than requiring a model deployment.
   [Official model-mode quickstart](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-quickstart).
5. **Select/create text resource.** Use an existing Azure OpenAI-capable resource.
   Otherwise Azure portal **Create a resource → Azure OpenAI → Create**; enter
   subscription, group, region, name and tier; configure network access for the
   test machine, then **Review + submit → Create**. **Keys and Endpoint** must
   expose an OpenAI resource origin for `AZURE_FOUNDRY_ENDPOINT`.
6. **Reasoning deployment.** Foundry classic **View all resources → select resource
   → Shared resources → Deployments → + Deploy model → Deploy base model**. For a
   Foundry resource the section can be **My assets → Models + endpoints**. Select
   `gpt-4.1`, version `2025-04-14`, name `deskmate-reasoning`, available Standard or
   Global Standard capacity, then **Deploy**; wait for **Succeeded**. An existing
   supported deployment can be used under its actual name. Do not pick a reasoning
   model with incompatible temperature/token settings.
7. **Embedding deployment.** Repeat deployment with `text-embedding-3-small`, name
   `deskmate-embedding`; wait for **Succeeded**. Configure 384 dimensions in the app,
   not by renaming the deployment. Steps 5–7 follow
   [Microsoft's deployment guide](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/create-resource).
   Model compatibility: [structured output models](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs#supported-models).
8. **Entra roles.** Each resource **Access control (IAM) → Add → Add role assignment
   → select role → Members → User, group, or service principal → Select members →
   your local test account → Review + assign**. Voice resource: **Cognitive Services
   User** and **Foundry User** (some portals still show Azure AI User). Text resource:
   **Cognitive Services OpenAI User**, covering chat and embeddings. An authorized
   administrator must assign roles; allow propagation before testing.
   [Voice identity](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to#authentication),
   [OpenAI role permissions](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/ai-machine-learning#cognitive-services-openai-user).
9. **Local sign-in.** Install [Azure CLI for macOS](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-macos)
   if needed (`brew install azure-cli`). Run `az login --output none` locally and
   select the authorized subscription in the sign-in flow. DefaultAzureCredential
   uses local identity; unexpected environment/service-principal credentials can
   take precedence over CLI. Never share token/debug output.
10. **Random cookie key.** Use the commands below from the repository root to write
    a private local key file without printing the secret. Open that file locally in
    your editor and copy its value into `VOICE_SESSION_SIGNING_KEY` there.
11. **Fresh stores.** The same commands create a new private directory. Copy its
    absolute path into `CASE_DB_PATH`/`CHROMA_DIR`. Keep old data and collections.
    Nothing in a previous local collection is automatically migrated.

    ```bash
    umask 077
    DESKMATE_LIVE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/deskmate-azure-live.XXXXXX")
    mkdir "$DESKMATE_LIVE_DIR/chroma"
    .venv/bin/python - "$DESKMATE_LIVE_DIR/session-signing-key" <<'PY'
    import secrets
    import sys
    from pathlib import Path
    target = Path(sys.argv[1])
    target.write_text(secrets.token_urlsafe(48))
    target.chmod(0o600)
    PY
    printf 'Private acceptance directory: %s\n' "$DESKMATE_LIVE_DIR"
    ```

12. **Local settings.** Edit the existing `.env` privately with the block above.
    Stop the fake launcher with Ctrl+C. Use a fresh shell without fake provider/path
    exports: shell environment overrides `.env`. Use one browser origin consistently
    (recommended `http://127.0.0.1:5173`) for creation, cookie, HTTP and WebSocket.
13. **Safe readiness.** Run the following. It constructs local MCP and initializes
    additive tables/the verified collection at your configured paths, then probes
    SQLite/Chroma locally. It sends no Azure or embedding request. Status output
    contains no settings values. Optional `--check-cli` inspects local account
    selection with captured output; it does not prove a token or service access.

    ```bash
    .venv/bin/python -m backend.app.diagnostics.azure_readiness
    ```

    With voice disabled, exit 1/voice configured NO is expected. Exit 2 means invalid
    configuration. ENTRA means the method is selected; FOUND means a setting exists.
    Neither means authenticated. Each cloud probe remains NOT_CHECKED/live NO.
14. **Normal startup.** Run `make dev`, open the chosen origin, then inspect
    `/readiness`. Expect runtime `azure`, local MCP/stores READY, text ready, and
    voice disabled. Health uses cached local proof and never contacts Azure. Never
    use `fake-voice-demo.sh` for Azure acceptance.
15. **Reasoning first (requires explicit live authorization).** Click **Start text
    support**. It creates an authenticated text-only session and asks Azure OpenAI
    to generate the greeting; it does not open Voice Live or a microphone. Send a
    fictional support question. Confirm errors are visible and no demo badge/canned
    response appears. Stop this session before proceeding.
16. **Embeddings second (requires authorization).** Through Paste ingestion add only
    the fictional VPN KB in [VOICE.md](VOICE.md). Query that document using Run
    DeskMate. Confirm ingestion/retrieval provenance in the new collection. Accept
    no prior unrelated evidence; public research remains relevance-gated. This
    proves actual embeddings separately from client initialization.
17. **One authorized voice test last.** After agreeing to live Azure usage and real
    microphone access, locally set `VOICE_ENABLED=true`, restart, and refresh
    readiness. Optionally run the explicit silent probe below first. Then follow
    [the acceptance sequence](VOICE.md#one-explicit-live-azure-acceptance) with one
    tester, headphones and fictional data. Click **Start voice chat**, grant
    permission and verify stages, speech/transcript agreement, contact correction,
    MCP retrieval, barge-in, mute/text fallback, explicit resolution and case save.
18. **Disable afterward.** Stop the session, confirm the browser microphone is off,
    set `VOICE_ENABLED=false` locally and restart. Preserve the acceptance stores
    for review. Disable cloud access/revoke credentials through your administrator
    if an emergency requires it; do not delete resources or customer data casually.

## Explicit silent live validation

After authorization, with complete Azure configuration and voice enabled:

```bash
.venv/bin/python -m backend.app.diagnostics.azure_readiness --live
```

This authorizes token acquisition, **one** 80-token structured reasoning request,
**one** fictional-text embedding and **one** Voice Live open/configure/close. It
waits for `session.updated`, sends no microphone frames, and never sends
`response.create`. The [SDK connect/session API](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-voicelive-readme?view=azure-python)
and [stable session events](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10)
support this configuration handshake independently of speech. It can incur Azure
usage; it does not validate speech fidelity, latency or spoken support quality.
Failures/cleanup are bounded and output stays status-only. The CLI report is a
point-in-time result; HTTP readiness does not inherit it or pretend it performed
those checks. There is no automatic retry or fake fallback.

## Shareable information and emergency stop

Safe to share: region, resource type, deployment/model names, whether roles are
assigned, readiness statuses and sanitized error codes. Never share API keys,
access tokens, cookie signing key, connection strings, `.env`, cookies or
Authorization headers. Endpoint values stay local; diagnostics never print them.

Emergency: **Stop session**, close the tab, or Ctrl+C on the application host.
An owner-authenticated same-origin POST to `/voice/sessions/{id}/stop` also stops
the session. Set `VOICE_ENABLED=false` and restart to block new audio sessions;
text support remains available when reasoning is configured. Roll back source
from the saved snapshot with voice disabled, preserving `.env` and all user stores.
See [VOICE.md](VOICE.md#emergency-stop-and-rollback).
