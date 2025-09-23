#!/usr/bin/env bash
set -euo pipefail

LOG_DIR=${LOG_DIR:-logs}
mkdir -p "$LOG_DIR"

cleanup() {
  printf '\n[dev] Stopping dev servers...\n'
  if [[ -n "${BACKEND_PID:-}" ]]; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  if [[ -n "${FRONTEND_PID:-}" ]]; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT

printf '[dev] Starting backend on http://localhost:8000 ...\n'
uvicorn backend.app.main:app --reload --port 8000 --log-config backend/logging.ini &
BACKEND_PID=$!

printf '[dev] Starting frontend on http://localhost:5173 ...\n'
(
  cd frontend
  npm run dev
) &
FRONTEND_PID=$!

wait
