# M1 · Gateway and the first real lanes (router + coder)

**Context.** M0 is done: Pablo has a working driver, CUDA, uv, Docker, and the harness runs in fake
mode. Read AGENTS.md and `config/litellm.yaml`. The harness talks only to the LiteLLM gateway on
`127.0.0.1:4000`; each lane is an OpenAI-compatible server on its own port.

**Goal.** Real models answering through the gateway: router on :8002, coder on :8001, and the harness
running a real task end to end (the judge temporarily reuses the coder model).

**Steps**
1. **Pick weights.** Find the current GGUF releases for the coder (Qwen3.6 35B-A3B instruct/coder
   variant) and the router (OpenJev 4B v2, or the closest small instruct model if that repo is gone).
   Confirm the license on each model card. Choose quants that fit: coder about Q4_K_M with the router
   resident beside it, with ≥ 2 GB VRAM headroom at 16k context. Write the choice and VRAM math in
   `docs/decisions.md`.
2. **Download** into `$PABLO_DATA/models/<name>/` (default `~/pablo-data`), then archive each with
   `bash scripts/archive-model.sh $PABLO_DATA/models/<name>` and fill in SOURCE.txt. Add rows to the
   model table in THIRD_PARTY.md.
3. **Build llama.cpp** with CUDA in `$PABLO_DATA/engines/llama.cpp` (pin a release tag; record it).
   Serve router on 127.0.0.1:8002 and coder on 127.0.0.1:8001 with `llama-server`, flash attention
   on, context 16384, and `--jinja` if the chat template needs it.
4. **Gateway.** Install LiteLLM in its own venv under `$PABLO_DATA/engines/litellm` (not the harness
   venv). Generate a random `PABLO_GATEWAY_KEY` into `config/.env` (gitignored). Start
   `litellm --config config/litellm.yaml --host 127.0.0.1 --port 4000`. Confirm telemetry is off.
5. **Wire up.** Add `scripts/start-lanes.sh` and `scripts/stop-lanes.sh` (tmux or systemd user units,
   your call; record it) to start/stop router, coder, and gateway. Make `pablo` load `config/.env`
   if present (small change in `config.py` plus a test).
6. **Smoke test.** `uv run pablo doctor` (gateway up, GPU shown). Then in `~/pablo-sandbox/hello`:
   `uv run pablo run "add a function mul(a, b) to the module with a test" -C ~/pablo-sandbox/hello`.
7. **Fix what breaks.** Likely spots: the coder not emitting a clean ```diff block (tune `CODER_PROMPT`
   in orchestrator.py), diffs failing `git apply` (add a retry that sends the apply error back once),
   judge JSON parsing. Every fix gets a FakeBackend test.
8. Switch the systemd `pablo` unit from `--fake` to real mode.

**Constraints.** Engines and LiteLLM bind 127.0.0.1 only. Never put weights inside the repo. Don't
start the judge/deep engines yet (that's M3).

**Acceptance**
- `uv run pablo doctor` → gateway up.
- The `mul` task produces a diff that applies, checks pass, judge certainty is shown, and approving it
  changes the sandbox repo. Repeat with 3 different small tasks; record pass/fail in `docs/benchmarks.md`
  with tokens/s and time to first token for both lanes.
- `nvidia-smi` during a coder run shows headroom ≥ 2 GB.
- Tests, ruff, mypy pass. BUILD_PLAN M1 marked done.

**Report back:** chosen quants + VRAM math, tokens/s, the 3 task results, anything flaky.
