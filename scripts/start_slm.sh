#!/usr/bin/env bash
set -euo pipefail

LOG_DIR=${LOG_DIR:-logs}
mkdir -p "$LOG_DIR"

info()  { printf '[slm] %s\n' "$*"; }
warn()  { printf '[slm][warn] %s\n' "$*"; }

if command -v ollama >/dev/null 2>&1; then
  info "Detected Ollama CLI." 
  if ! pgrep -f "ollama serve" >/dev/null 2>&1; then
    info "Starting 'ollama serve' in the background..."
    nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
    sleep 2
  else
    info "'ollama serve' already running."
  fi
  info "You can pull a model with: ollama pull \"${MODEL_NAME:-tinyllama}\""
  exit 0
fi

LLAMACPP_BIN=${LLAMACPP_BIN:-}
MODEL_PATH=${MODEL_PATH:-}
if [[ -n "$LLAMACPP_BIN" ]]; then
  if [[ ! -x "$LLAMACPP_BIN" ]]; then
    warn "LLAMACPP_BIN is set but not executable: $LLAMACPP_BIN"
  elif [[ -z "$MODEL_PATH" ]]; then
    warn "Provide MODEL_PATH to launch llama.cpp server."
  else
    info "Starting llama.cpp server on :8080..."
    nohup "$LLAMACPP_BIN" -m "$MODEL_PATH" --port 8080 --host 127.0.0.1 >"$LOG_DIR/llamacpp.log" 2>&1 &
    exit 0
  fi
fi

warn "No local SLM detected. Install Ollama (https://ollama.ai) for the quickest setup."
warn "Alternatively, export LLAMACPP_BIN and MODEL_PATH to start a llama.cpp server."
exit 0
