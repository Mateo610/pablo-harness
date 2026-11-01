#!/usr/bin/env bash
# Install and start the router, coder, and LiteLLM gateway as systemd user units.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
env_file="$root/config/.env"

if [[ ! -f "$env_file" ]]; then
  echo "missing $env_file (PABLO_GATEWAY_KEY)" >&2
  exit 1
fi

mkdir -p "$unit_dir"
for unit in pablo-coder.service pablo-router.service pablo-gateway.service; do
  cp "$root/scripts/systemd/$unit" "$unit_dir/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now pablo-router.service pablo-coder.service pablo-gateway.service
systemctl --user --no-pager --lines=0 status pablo-router.service pablo-coder.service pablo-gateway.service
