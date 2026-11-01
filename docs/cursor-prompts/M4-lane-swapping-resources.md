# M4 · Lane swapping and the resources panel

**Context.** Lanes now run on 8001–8004, but they can't all be resident in 24 GB. The router (small)
should stay loaded; coder, judge, and deep must swap. llama-swap (MIT) is a proxy that starts and
stops model servers by requested model name; check whether it can also launch the KTransformers/colibrì
servers via its command config. If not, write a small swapper in `src/pablo/swap.py`.

**Goal.** Any lane can be requested at any time without OOM, and the cockpit shows what is loaded.

**Steps**
1. Put the swapper on 127.0.0.1:8100, configured so `router` is always resident and coder/judge/deep
   are mutually exclusive groups. Point LiteLLM's lane aliases at the swapper.
2. Add `GET /api/resources` to `api.py`: GPU name, VRAM used/total, RAM used/total, loaded lanes,
   per-lane last load time. Use `nvidia-smi --query-gpu` and `/proc/meminfo`; no new heavy deps.
   Tests use a fake reader.
3. Make the orchestrator emit a `status` event "judge · loading" when a swap is expected (the swapper's
   response time or a `/running` endpoint can tell you), so the cockpit status line is honest.
4. Soak test: `scripts/soak.py` runs 20 mixed runs (coder → judge alternating, 2 deep) against the
   sandbox repo. Record swap times and any failures in `docs/benchmarks.md`.

**Acceptance**
- 20-run soak finishes with 0 OOM or engine crashes.
- `curl 127.0.0.1:8710/api/resources` returns live numbers.
- Tests, ruff, mypy pass. BUILD_PLAN M4 marked done. (The resources panel UI is core track.)
