# M0 · Pablo baseline

**Context.** You are working on Pablo, my home server (Ubuntu, RTX 3090 24 GB, 64 GB DDR4, 2 TB
WD SN7100 NVMe), over Remote-SSH. This repo is the Pablo harness; read AGENTS.md first. Nothing
GPU-related is running yet. An older project lives at `~/ai-coding-team`; do not modify it.

**Goal.** Get Pablo to a clean, known-good baseline and prove the harness runs here in fake mode,
reachable from my laptop over Tailscale only.

**Steps**
1. Run `bash scripts/bootstrap-pablo.sh` and show me the report.
2. For each missing item, tell me the exact command. I run anything that needs `sudo`. Priorities:
   NVIDIA driver working in `nvidia-smi`; CUDA toolkit whose version the driver supports; build tools
   (`build-essential cmake`); `uv`; Node 22 LTS; Docker + NVIDIA container toolkit; Tailscale up.
3. Confirm the NVMe layout: where `/` and `$HOME` live, free space, and whether models should go to
   `~/pablo-data` (default) or a separate mount. Write the decision to `docs/decisions.md`.
4. `uv sync`, then `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy`. All must pass.
5. `cd web && npm install && npm run build`.
6. Create a scratch project for experiments: `~/pablo-sandbox/hello` with a git repo, a tiny Python
   module, one passing pytest test, and `uv run pablo init -C ~/pablo-sandbox/hello`.
7. Start the harness in fake mode bound to the Tailscale IP:
   `uv run pablo serve --fake -C ~/pablo-sandbox/hello --host $(tailscale ip -4 | head -1)`
8. Add a user systemd unit `~/.config/systemd/user/pablo.service` that runs the same command (fake mode
   for now), `systemctl --user enable --now pablo`, and `loginctl enable-linger $USER` (I run the sudo
   part if needed). Put the unit file in `scripts/systemd/pablo.service` too.

**Constraints.** Never bind 0.0.0.0. Do not open router ports or use Tailscale Funnel. Do not install
model weights in this milestone.

**Acceptance**
- `bash scripts/bootstrap-pablo.sh` ends with "Pablo is ready for Milestone 1."
- From my laptop browser, `http://<tailscale-ip>:8710` shows the cockpit; I submit "add a demo note",
  approve it, and `PABLO_DEMO.md` appears in `~/pablo-sandbox/hello`.
- `curl http://<LAN-ip>:8710/api/health` from another LAN device fails (not exposed outside Tailscale).
- Tests, ruff, mypy pass. `docs/BUILD_PLAN.md` M0 marked done.

**Report back:** bootstrap output before/after, driver + CUDA versions, disk layout decision.
