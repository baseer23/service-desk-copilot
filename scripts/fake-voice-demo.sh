#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TASK_RUNTIME_DIR=$(mktemp -d "${TMPDIR:-/tmp}/deskmate-fake-voice.XXXXXX")
export TEST_MODE=true GENERATION_PROVIDER=stub EMBED_PROVIDER=stub VOICE_PROVIDER=fake
export AZURE_USAGE_GUARD_ENABLED=false AZURE_EMERGENCY_DISABLE=true
export AZURE_LIVE_TEST_MODE=false AZURE_SETUP_VALIDATION_MODE=false
export VOICE_ENABLED=true NEO4J_URI='' VOICE_AUTH_MODE=local_demo DEPLOYMENT_MODE=local
export KNOWLEDGE_SCOPE=support RESEARCH_SOURCE_POLICY=prefer_primary
export MAX_INVESTIGATION_CYCLES=8 MAX_RESEARCH_ROUNDS=0 ALLOW_URL_INGEST=false
export CASE_DB_PATH="$TASK_RUNTIME_DIR/cases.db" CHROMA_DIR="$TASK_RUNTIME_DIR/chroma"
export AZURE_USAGE_DB_PATH="$TASK_RUNTIME_DIR/azure-usage.sqlite3"
export VOICE_SESSION_SIGNING_KEY
VOICE_SESSION_SIGNING_KEY=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')
printf 'Isolated fake voice storage: %s\n' "$TASK_RUNTIME_DIR"
exec make dev
