# M4 · Lane swapping and the resources panel

**Context.** Lanes now run on 8001–8004, but they can't all be resident in 24 GB. The router (small)
should stay loaded; coder, judge, and deep must swap. llama-swap (MIT) is a proxy that starts and
stops model servers by requested model name; check whether it can also launch the KTransformers/colibrì
servers via its command config. If not, write a small swapper in `src/pablo/swap.py`.

**Goal.** Any lane can be requested at any time without OOM, and the cockpit shows what is loaded.

**Where M3 left things (read `docs/decisions.md`).** All three lanes are now `llama-server`: coder
(Qwen3.6 35B-A3B, ~20 GB VRAM), judge (Mistral-Small-3.2-24B heretic Q5, 18.4 GB peak on GPU, but
currently `-ngl 0` on CPU at ~2.4 tok/s), router (Qwen3.5-4B, CPU). The judge on CPU makes every run
wait minutes, so the first win of M4 is moving the judge to the GPU at ~46 tok/s by swapping it with the
coder. Since every lane is llama.cpp, llama-swap can launch them all directly.

**Swap rules.** Router stays on CPU, always loaded, outside the swap group. Coder and judge are one
exclusive GPU group; the coder is the default resident. A run goes coder → judge → back to coder.
Measure the swap cost both ways; with 64 GB RAM both GGUFs (~36 GB) should stay in the page cache, so
reloads should be seconds. If a swap takes more than ~15 s, record it and try `--mlock`/`--no-mmap`
tradeoffs. Overnight queue jobs may batch: run all coder steps, then all judge steps.

**Steps**
1. Put the swapper on 127.0.0.1:8100, configured so `router` is always resident and coder/judge/deep
   are mutually exclusive groups. Point LiteLLM's lane aliases at the swapper.
2. `GET /api/resources` already exists (`src/pablo/resources.py`, built in the core track). Fill in
   `loaded` from the swapper (llama-swap's running-models endpoint) and add a test with a fake reader.
3. Make the orchestrator emit a `status` event "judge · loading" when a swap is expected (the swapper's
   response time or a `/running` endpoint can tell you), so the cockpit status line is honest.
4. Soak test: `scripts/soak.py` runs 20 mixed runs (coder → judge alternating, 2 deep) against the
   sandbox repo. Record swap times and any failures in `docs/benchmarks.md`.

**Acceptance**
- 20-run soak finishes with 0 OOM or engine crashes.
- `curl 127.0.0.1:8710/api/resources` returns live numbers.
- Tests, ruff, mypy pass. BUILD_PLAN M4 marked done. (The resources panel UI is core track.)
