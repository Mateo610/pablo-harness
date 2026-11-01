#!/usr/bin/env bash
# Stop the swapper and the LiteLLM gateway. Leaves `pablo serve` running.
# Also stops the retired per-lane units if a leftover is still up.
set -euo pipefail

systemctl --user stop pablo-gateway.service pablo-swap.service
for old in pablo-coder.service pablo-judge.service pablo-router.service; do
  systemctl --user stop "$old" >/dev/null 2>&1 || true
done
echo "stopped pablo-gateway, pablo-swap"
