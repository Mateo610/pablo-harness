#!/usr/bin/env bash
# Install and start llama-swap and the LiteLLM gateway as systemd user units.
# The old per-lane units are stopped so they cannot share the GPU with the swapper.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
env_file="$root/config/.env"

if [[ ! -f "$env_file" ]]; then
  echo "missing $env_file (PABLO_GATEWAY_KEY)" >&2
  exit 1
fi

mkdir -p "$unit_dir"
for unit in pablo-swap.service pablo-gateway.service; do
  cp "$root/scripts/systemd/$unit" "$unit_dir/$unit"
done
systemctl --user daemon-reload
for old in pablo-coder.service pablo-judge.service pablo-router.service; do
  systemctl --user disable --now "$old" >/dev/null 2>&1 || true
done
systemctl --user enable --now pablo-swap.service pablo-gateway.service
systemctl --user --no-pager --lines=0 status pablo-swap.service pablo-gateway.service
