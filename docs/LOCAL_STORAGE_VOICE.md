# Local persistence and repeatable voice sessions

The Mac profile uses `DATA_PLATFORM=local` and `LOCAL_PERSISTENCE_ENABLED=true`.
Azure OpenAI generates reasoning/embeddings, Document Intelligence extracts layout,
and Voice Live transcribes/speaks. Cosmos, Blob and Search are not constructed by
this profile. Historical adapters remain for export and rollback; the older
production configuration still requires its managed persistence settings.

## Data locations

- `store/local/chroma/`: primary verified text/vectors. Tenant metadata filters
  apply to every query. Older embedding spaces remain in separate `archive_*`
  collections and are searched lexically, without mixing vector spaces.
- `store/local/documents/`: immutable originals, `extraction.json` and `vectors.json`,
  partitioned by tenant hash/document hash. Original PDFs remain binary-identical.
- `store/local/state.sqlite3`: transactional cases, events, facts, owner-scoped
  conversations/transcripts, leases, checkpoints, document manifests/jobs and the
  complete migrated financial ledger. Pending unembedded knowledge belongs to
  ingestion jobs; no substitute vectors are generated.
- `store/local/migration-report.private.json`: verified counts and source manifest
  hash. Private data and backups are ignored by Git. Directories are private;
  artifact and database files are mode 600.

The verified source snapshot is `store/backups/azure-local-20261014-verified/`.
A second verified backup of the complete migrated stores, including the new
voice transcripts and current financial policy, is
`store/backups/local-20261014-after-voice/` (2,119 transactional records).
Its manifest verifies every exported file; Cosmos versions were stable across
the export. It includes all 1,967 cloud records, 7 Search chunks, all 27 blobs
(including deployment backups), and both earlier local stores. Six document
vectors came from saved vector artifacts and the remaining vector from the
verified earlier Chroma store; no embedding calls were needed.

The local import preserved all cloud records, 15 document artifacts, 7 current
vectors and 4 earlier vectors. It also imported earlier local cases, events,
facts, one document and 31 checkpoints. Older differing case versions remain
separate historical records; neither source was overwritten. Existing transcript
tenant/owner IDs and the signing key are preserved. Access still requires the
original authorized identity; migration does not grant one owner another's history.
An earlier document that was only saved locally remains saved without automatically
submitting it to Azure. The complete original databases also remain in the backup.

## Repeatable two-minute voice sessions

`AZURE_MAX_VOICE_SESSION_MINUTES=2` and `VOICE_SESSION_TIMEOUT_SEC=120` bound each
session. A deadline cancels streaming, closes Azure, saves a normal `stopped` state,
clears playback and releases its lease. The SDK duration watchdog produces the
same normal outcome. Neither normal stop nor acceptance mode calls `disable()`.

`AZURE_MAX_LIVE_SESSIONS_PER_DAY=0` removes the daily session-count restriction.
It does not remove the monthly estimate limit, reservation accounting, one-session
concurrency rule, per-session turn limit or persistent emergency controls. Every
new session must still fit the approved remaining allowance. At the export,
CAD 0.308125 remained under the explicitly approved CAD 151 limit, including the
existing CAD 90 infrastructure forecast. Three real two-minute sessions need at
least CAD 3 plus reasoning. The user subsequently explicitly approved CAD 155
for the three-session test and further manual testing. The active local policy
was updated with an audit reference; historical charge fingerprints, emergency
state and the CAD 90 forecast were verified unchanged.

`VOICE_TRANSCRIPT_RETENTION_HOURS=0` retains the migrated transcripts on the private
Mac. A nonzero value enables the existing lazy retention policy. Raw audio is
never persisted. Keep a separate encrypted backup appropriate for your data.

Voice rendering instructions: `backend/app/voice/prompts/support_v1.md`.
Conversation wording/behavior also uses the files in `backend/app/voice/prompts/`
through `backend/app/voice/graph.py`. Restart `make dev` and start a new conversation
after editing prompts, since they are loaded into the backend process.

## Reproduce migration and verify

Stop writers before the final snapshot. Use fresh destinations; never run an
export against the new local profile with blank cloud endpoints. Use the private
pre-cutover configuration for a new read-only export when necessary.

```bash
PYTHONPATH=. .venv/bin/python scripts/export_azure_local.py --output PRIVATE_FRESH_BACKUP
PYTHONPATH=. .venv/bin/python scripts/migrate_azure_local.py --backup PRIVATE_FRESH_BACKUP --output PRIVATE_FRESH_LOCAL_ROOT
```

The importer checks all hashes, stable source versions, original artifacts,
float32 vector equivalence, metadata, checkpoint pending writes and complete
financial history. It fails on ambiguous vectors, differing financial history or
existing target stores. It never contacts Azure, generates embeddings, deletes
source data, or enables an emergency-disabled policy. Source Chroma and SQLite
checkpoint databases are read through scratch copies so verification cannot alter
the backup. `.env` cutover is separate and its original private copy is retained.

Run only the related checks for this change:

```bash
.venv/bin/python -m pytest backend/app/tests/test_local_persistence.py backend/app/tests/test_voice_repeated_sessions.py backend/app/tests/test_azure_migration_documents.py backend/app/tests/test_azure_usage.py backend/app/tests/test_voice_sdk.py backend/app/tests/test_browser_voice.py backend/app/tests/test_managed_voice_io.py backend/app/tests/test_local_managed_voice_owner.py backend/app/tests/test_local_documents.py backend/app/tests/test_azure_data_platform.py backend/app/tests/test_azure_readiness.py backend/app/tests/test_voice_conversation_latency.py -q
npm --prefix frontend test -- src/VoiceSupport.test.tsx src/voiceAudio.test.ts src/KnowledgePanel.test.tsx
```

Verify real HTTP downloads and keyword searches, record logical database and file
hashes, restart `make dev`, then compare hashes and repeat HTTP checks. Fake transport
tests demonstrate lifecycle behavior; they do not establish real Azure speech
acceptance. With explicit spending authorization, run three consecutive full
120-second real Azure sessions, verify recognized speech and audible response in
each, automatic normal stop, no active reservation/lease left, and immediate next
start. Physical microphone testing requires authorized browser access.

## Cloud costs and rollback

The legacy Cosmos account has free tier enabled and 2,000 shared provisioned RU/s.
Cosmos cannot pause. Reducing it to 1,000 RU/s retains the data within the account's
free throughput allowance; the first 25 GB are also covered. See Microsoft's
[free-tier rules](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier).
The proposed exact deactivation/throughput commands are in the private review file
`/private/tmp/deskmate-repair-jqryhn51/local-cutover-resource-plan.txt` and were explicitly approved and executed after local verification. The hosted
app now has zero active revisions; Cosmos throughput reads back as 1,000 RU/s.
A final cloud record-version check still matched all 1,967 backed-up records.
No deletion was performed.
Search is already Free. Retained Blob backups can still incur storage charges.
Local cutover alone does not stop Azure billing or reconcile forecast reservations.

Before restoring anything, stop the application and snapshot the current local
stores, including SQLite committed WAL state. Never replace a live SQLite database
or merge Chroma directories. Restore a complete verified snapshot into fresh
paths and update configuration privately. Keep the stable signing key and tenant
IDs. Restore **current** financial history, reservations and emergency status;
an older backup must never reduce accounted spending. Do not reactivate the old
cloud app until any new local writes have been reconciled, or it would create a
second divergent writer. Cloud capacity changes and reactivation require their own
review. Keep Azure AI resources; stopping persistence must not delete them.

For immediate hang-up, use **Stop** in Voice Support. For an actual fault, the
operator endpoint `POST /admin/azure/usage/disable` persists shutdown and closes
active sessions. Restarting the app does not clear that emergency control.

## Verified outcome — 2026-10-14

- 160 targeted backend checks; 23 focused frontend checks; 5 storage/voice
  Playwright scenarios passed. No UI components were redesigned. The isolated
  voice browser test now seeds its own knowledge rather than depending on another
  scenario's earlier ingestion. Existing unrelated lint debt was left unchanged;
  the patch introduces no new lint diagnostics.
- Real HTTP/UI checks loaded six documents, downloaded every original and found
  cited keyword matches without generation. Across an application restart, all
  2,115 pre-acceptance local records and 16 artifact files were identical; a new
  local verification write also survived restart. All three original PDFs were
  preserved. Older extracted-only staging had no recoverable binary PDF; its text
  and complete source database were retained.
- Three consecutive real Azure Voice Live sessions ran through **Start Voice Chat**
  in authorized Chrome, using real PCM streaming, server VAD, transcription,
  Azure structured reasoning and non-silent spoken playback. All recognized two
  fictional prerecorded utterances and produced spoken responses. Stop snapshots
  arrived at 120.573, 120.417 and 120.401 seconds. Each ended as `stopped` with
  `duration_limit`, stopped every media track and immediately enabled the next
  start. No browser/provider errors occurred.
- Input was OS-generated fictional speech. The physical microphone was opened
  and muted before capture; human pronunciation, room acoustics and microphone
  signal quality were not tested. No microphone recordings were saved.
- All three reservations finished and every lease was released. Original cloud
  payloads and every historical financial charge remained unchanged. The guard
  stayed enabled, with CAD 1.168135 left under CAD 155; `/readiness` reported
  `voice_ready=true`. The finite spending allowance still governs further calls.

Private evidence is in `/private/tmp/deskmate-repair-jqryhn51/`:
`three-real-voice-sessions.json`, `three-real-voice-postconditions.json`,
`local-storage-browser.json`, `local-persistence-before.private.json`, and the
focused validation logs. These artifacts contain operational/private data and
are not committed.
