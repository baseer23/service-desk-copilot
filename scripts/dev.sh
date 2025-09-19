#!/usr/bin/env bash
set -euo pipefail

# Dev helper to run backend and frontend together.
# - Backend: FastAPI with auto-reload on port 8000
# - Frontend: Vite dev server on port 5173
#
# Usage: ./scripts/dev.sh

echo "Starting backend (uvicorn) on :8000..."
uvicorn backend.app.main:app --reload --port 8000 &
BACKEND_PID=$!

echo "Starting frontend (Vite) on :5173..."
(
  cd frontend
  npm run dev
) &
FRONTEND_PID=$!

cleanup() {
  echo "\nStopping dev servers..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
}

trap cleanup INT TERM
wait

