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
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

printf '[dev] Starting frontend on http://localhost:5173 ...\n'
(
  cd frontend
  npm run dev -- --host 127.0.0.1
) &
FRONTEND_PID=$!

wait
