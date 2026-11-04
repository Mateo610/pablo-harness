#!/usr/bin/env bash
# M10 offline drill: block egress, restart Pablo, run one suite pass.
# Tailscale (tailscale0) and loopback stay up so the cockpit still reaches you.
# Escalation to Anthropic must fail; every local lane must still answer.
#
# Run as your login user (nftables calls sudo internally):
#   bash scripts/offline-drill.sh
#   bash scripts/offline-drill.sh --undo
set -euo pipefail

TABLE=pablo_offline
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${PABLO_OFFLINE_LOG:-/tmp/pablo-offline-drill.log}"
# If someone `sudo`s the whole script, bounce back to the login user for
# systemctl --user and uv (root has no user systemd session for Pablo).
if [[ "${EUID}" -eq 0 ]]; then
  RUN_USER="${SUDO_USER:-}"
  if [[ -z "$RUN_USER" || "$RUN_USER" == root ]]; then
    echo "run as your login user, or: sudo -u <you> bash $0" >&2
    exit 2
  fi
  exec sudo -u "$RUN_USER" -E bash "$0" "$@"
fi

nft_sudo() { sudo nft "$@"; }

undo() {
  nft_sudo delete table inet "$TABLE" 2>/dev/null || true
  echo "removed nftables table inet $TABLE"
}

block() {
  undo
  nft_sudo -f - <<EOF
table inet $TABLE {
  chain output {
    type filter hook output priority 0; policy accept;
    oif "lo" accept
    oifname "tailscale0" accept
    ct state established,related accept
    meta l4proto { tcp, udp } reject with icmpx type admin-prohibited
  }
}
EOF
  echo "egress blocked (lo + tailscale0 still open)"
}

restart_pablo() {
  systemctl --user restart pablo-swap.service
  systemctl --user restart pablo-gateway.service
  systemctl --user restart pablo.service
  # Give llama-swap time to bring the router back.
  sleep 8
  systemctl --user is-active pablo-swap.service pablo-gateway.service pablo.service
}

smoke_egress() {
  echo "egress smoke (should fail):"
  if curl -fsS --max-time 5 https://example.com >/dev/null 2>&1; then
    echo "FAIL: example.com still reachable" >&2
    return 1
  fi
  echo "  example.com unreachable (good)"
  echo "loopback smoke (should pass):"
  # Wait for swap after restart.
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 http://127.0.0.1:8100/running >/dev/null 2>&1; then
      echo "  llama-swap /running ok"
      return 0
    fi
    sleep 1
  done
  echo "FAIL: llama-swap not answering on :8100" >&2
  return 1
}

run_suite() {
  cd "$REPO"
  export PABLO_DATA="${PABLO_DATA:-$HOME/pablo-data}"
  # One suite pass proves offline; the 3-run rematch for clip is after --undo.
  uv run pablo eval --write 2>&1 | tee -a "$LOG"
}

case "${1:-}" in
  --undo) undo; exit 0 ;;
  --block-only) block; exit 0 ;;
  --smoke) smoke_egress; exit 0 ;;
  "")
    : >"$LOG"
    {
      echo "=== offline drill $(date -Is) ==="
      block
      restart_pablo
      smoke_egress
      run_suite
      echo "=== suite done; lifting block ==="
      undo
      echo "DONE $(date -Is)"
    } 2>&1 | tee -a "$LOG"
    ;;
  *)
    echo "usage: $0 [--undo|--block-only|--smoke]" >&2
    exit 2
    ;;
esac
