#!/usr/bin/env bash
# Candidate coder: base GGUF + the latest trained adapter.gguf.
set -euo pipefail
PABLO_DATA="${PABLO_DATA:-$HOME/pablo-data}"
LLAMA="$PABLO_DATA/engines/llama.cpp/build/bin/llama-server"
MODEL="$PABLO_DATA/models/qwen3.6-35b-a3b/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf"
ADAPTER="${PABLO_CODER_CANDIDATE_LORA:-$PABLO_DATA/adapters/coder-candidate/adapter.gguf}"
PORT="${1:?port}"
shift || true

if [[ ! -f "$ADAPTER" ]]; then
  echo "llama-coder-candidate: missing adapter at $ADAPTER" >&2
  exit 1
fi

exec "$LLAMA" \
  -m "$MODEL" \
  --host 127.0.0.1 --port "$PORT" \
  -c 16384 -ngl 99 \
  -ctk q8_0 -ctv q8_0 \
  --n-cpu-moe 2 \
  --flash-attn on \
  --jinja \
  --reasoning off \
  --alias coder-candidate \
  --lora "$ADAPTER" \
  "$@"
