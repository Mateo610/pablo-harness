#!/usr/bin/env bash
# Stop the router, coder, and LiteLLM gateway. Leaves `pablo serve` running.
set -euo pipefail

systemctl --user stop pablo-gateway.service pablo-coder.service pablo-router.service
echo "stopped pablo-gateway, pablo-coder, pablo-router"
