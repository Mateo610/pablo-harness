#!/usr/bin/env bash
# Start the coder llama-server. Adds --lora when an active GGUF adapter exists.
set -euo pipefail
PABLO_DATA="${PABLO_DATA:-$HOME/pablo-data}"
LLAMA="$PABLO_DATA/engines/llama.cpp/build/bin/llama-server"
MODEL="$PABLO_DATA/models/qwen3.6-35b-a3b/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf"
ADAPTER="${PABLO_CODER_LORA:-$PABLO_DATA/adapters/coder-active/adapter.gguf}"
PORT="${1:?port}"
shift || true
# llama.cpp keeps the first flag when one is repeated. coder-think sets these
# instead of appending --reasoning on / -c, which the server would ignore.
REASONING="${PABLO_CODER_REASONING:-off}"
CONTEXT="${PABLO_CODER_CONTEXT:-16384}"

args=(
  -m "$MODEL"
  --host 127.0.0.1 --port "$PORT"
  -c "$CONTEXT" -ngl 99
  -ctk q8_0 -ctv q8_0
  --n-cpu-moe 2
  --flash-attn on
  --jinja
  --reasoning "$REASONING"
  --alias coder
)
if [[ -f "$ADAPTER" ]]; then
  args+=(--lora "$ADAPTER")
  echo "llama-coder: loading LoRA $ADAPTER" >&2
fi
exec "$LLAMA" "${args[@]}" "$@"
