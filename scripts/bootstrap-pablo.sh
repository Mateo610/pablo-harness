#!/usr/bin/env bash
# Phase 0 check for Pablo. Safe to run any time: it only reads, unless you pass --install.
#   bash scripts/bootstrap-pablo.sh            # report what's ready and what's missing
#   bash scripts/bootstrap-pablo.sh --install  # also install the basics (asks for sudo)
set -uo pipefail

INSTALL=0
[[ "${1:-}" == "--install" ]] && INSTALL=1
DATA="${PABLO_DATA:-$HOME/pablo-data}"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; MISSING=$((MISSING + 1)); }
fix()  { printf '      fix: %s\n' "$*"; }
MISSING=0

echo "Pablo bootstrap · $(date '+%Y-%m-%d %H:%M')"

echo "System"
. /etc/os-release 2>/dev/null && ok "$PRETTY_NAME" || warn "unknown OS"
ram_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
[[ $ram_gb -ge 60 ]] && ok "RAM ${ram_gb} GB" || warn "RAM ${ram_gb} GB (expected 64)"

echo "GPU"
if command -v nvidia-smi >/dev/null; then
  ok "$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader)"
  cuda=$(nvidia-smi | grep -o 'CUDA Version: [0-9.]*' | cut -d' ' -f3)
  [[ -n "$cuda" ]] && ok "driver supports CUDA $cuda"
else
  warn "nvidia-smi not found"
  fix "sudo ubuntu-drivers install && sudo reboot"
fi
if command -v nvcc >/dev/null; then ok "nvcc $(nvcc --version | grep -o 'release [0-9.]*')"
else warn "CUDA toolkit (nvcc) missing; needed to build llama.cpp / KTransformers"
  fix "install the CUDA toolkit matching your driver from developer.nvidia.com (or: sudo apt install nvidia-cuda-toolkit)"
fi

echo "Disk"
df -h --output=source,size,avail,target / "$HOME" 2>/dev/null | sed 's/^/  /' | uniq
lsblk -d -o NAME,MODEL,SIZE,ROTA 2>/dev/null | sed 's/^/  /'
mkdir -p "$DATA"/{models,engines,archive,traces,benchmarks} && ok "data dirs at $DATA"

echo "Tools"
for tool in git curl tmux htop jq; do
  command -v $tool >/dev/null && ok "$tool" || { warn "$tool missing"; fix "sudo apt install -y $tool"; }
done
command -v cmake >/dev/null && ok "cmake" || { warn "cmake missing"; fix "sudo apt install -y build-essential cmake"; }
command -v uv >/dev/null && ok "uv $(uv --version | cut -d' ' -f2)" || { warn "uv missing"; fix "curl -LsSf https://astral.sh/uv/install.sh | sh"; }
command -v node >/dev/null && ok "node $(node --version)" || { warn "node missing (needed for the cockpit build)"; fix "install Node 22 LTS via nvm or nodesource"; }
command -v docker >/dev/null && ok "docker $(docker --version | cut -d' ' -f3 | tr -d ,)" || { warn "docker missing"; fix "https://docs.docker.com/engine/install/ubuntu/"; }
if command -v docker >/dev/null && docker info 2>/dev/null | grep -qi nvidia; then ok "NVIDIA container runtime"
else warn "NVIDIA container toolkit not detected"; fix "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"; fi

echo "Network"
if command -v tailscale >/dev/null; then
  ts_ip=$(tailscale ip -4 2>/dev/null | head -1)
  [[ -n "$ts_ip" ]] && ok "tailscale up at $ts_ip (serve the cockpit on this IP, never 0.0.0.0)" || warn "tailscale installed but not up"
else warn "tailscale missing"; fix "curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"; fi
systemctl is-active --quiet ssh && ok "ssh running" || warn "ssh not running"

if [[ $INSTALL -eq 1 ]]; then
  echo "Installing basics"
  sudo apt-get update -y && sudo apt-get install -y git curl tmux htop jq build-essential cmake
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo
[[ $MISSING -eq 0 ]] && echo "Pablo is ready for Milestone 1." || echo "$MISSING item(s) to fix. Re-run after fixing."
