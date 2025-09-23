#!/usr/bin/env bash
set -euo pipefail

LOG_DIR=${LOG_DIR:-logs}
mkdir -p "$LOG_DIR"

info()  { printf '[slm] %s\n' "$*"; }
warn()  { printf '[slm][warn] %s\n' "$*"; }

PREFERRED_MODELS=("phi3:mini" "tinyllama")
MODEL_REASONS=(
  "Phi 3 Mini chosen for Mac Air responsiveness."
  "TinyLlama fallback when thermals climb."
)

pick_ollama_model() {
  local output models idx=0
  if ! output=$(ollama list 2>/dev/null); then
    return 1
  fi
  models=$(printf '%s\n' "$output" | awk 'NR>1 {print $1}')
  for candidate in "${PREFERRED_MODELS[@]}"; do
    if printf '%s\n' "$models" | grep -q "^${candidate}$"; then
      printf '%s\n' "$idx"
      return 0
    fi
    idx=$((idx + 1))
  done
  return 1
}

if command -v ollama >/dev/null 2>&1; then
  info "Detected Ollama CLI." 
  if ! pgrep -f "ollama serve" >/dev/null 2>&1; then
    info "Starting 'ollama serve' in the background..."
    nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
    sleep 2
  else
    info "'ollama serve' already running."
  fi
  if model_index=$(pick_ollama_model); then
    SELECTED_MODEL=${PREFERRED_MODELS[$model_index]}
    REASON=${MODEL_REASONS[$model_index]}
    info "Active model: ${SELECTED_MODEL} (${REASON})"
    info "Set MODEL_PROVIDER=ollama and MODEL_NAME=${SELECTED_MODEL} for the backend."
  else
    primary=${PREFERRED_MODELS[0]}
    fallback=${PREFERRED_MODELS[1]}
    warn "No Phi 3 Mini or TinyLlama detected."
    warn "Pull one with: ollama pull \"${primary}\" (fallback: ${fallback})."
    warn "Backend will continue with the deterministic stub until a small model is available."
  fi
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
