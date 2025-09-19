#!/usr/bin/env bash
set -euo pipefail

# Start a local small language model (SLM) provider if available.
# Priority: Ollama -> llama.cpp server -> instructions.

MODEL_NAME=${MODEL_NAME:-tinyllama}
LOG_DIR=${LOG_DIR:-logs}
mkdir -p "$LOG_DIR"

info()  { echo "[slm] $*"; }
warn()  { echo "[slm][warn] $*"; }

if command -v ollama >/dev/null 2>&1; then
  info "Ollama CLI found. Ensuring 'ollama serve' is running..."
  if ! pgrep -f "ollama serve" >/dev/null 2>&1; then
    nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
    info "Started ollama serve (logs: $LOG_DIR/ollama.log). Waiting a moment..."
    sleep 2
  else
    info "ollama serve already running."
  fi

  # Check models and pull if missing
  if ! ollama list | awk '{print $1}' | grep -q "^${MODEL_NAME}$"; then
    info "Model '${MODEL_NAME}' not found. Pulling..."
    if ollama pull "${MODEL_NAME}"; then
      info "Model '${MODEL_NAME}' pulled successfully."
    else
      warn "Failed to pull model '${MODEL_NAME}'. You can try a different MODEL_NAME."
    fi
  else
    info "Model '${MODEL_NAME}' already available."
  fi

  info "Ollama ready on http://localhost:11434 (model: ${MODEL_NAME})."
  exit 0
fi

# Fallback: llama.cpp server
LLAMACPP_BIN=${LLAMACPP_BIN:-""}
if [[ -z "$LLAMACPP_BIN" && -x ./llama.cpp ]]; then
  LLAMACPP_BIN=./llama.cpp
fi

if [[ -n "$LLAMACPP_BIN" ]]; then
  if [[ -z "${MODEL_PATH:-}" ]]; then
    warn "LLAMACPP_BIN is set but MODEL_PATH not provided. Set MODEL_PATH to your .gguf model file."
    warn "Example: MODEL_PATH=./models/your-model.gguf $0"
    exit 0
  fi
  info "Starting llama.cpp server on :8080 using $LLAMACPP_BIN"
  nohup "$LLAMACPP_BIN" -m "$MODEL_PATH" --port 8080 --host 127.0.0.1 >"$LOG_DIR/llamacpp.log" 2>&1 &
  info "llama.cpp server started (logs: $LOG_DIR/llamacpp.log)."
  exit 0
fi

warn "No local SLM provider found. Install Ollama for easiest setup:"
warn "  brew install ollama && ollama run ${MODEL_NAME}"
warn "Then run: make slm"
exit 0

