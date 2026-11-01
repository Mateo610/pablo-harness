# M3 · Judge and deep engines, measured

**Context.** M1 runs router + coder on llama.cpp. The judge (DeepSeek V4 Flash REAP ~150B MoE) and the
deep lane (GLM-5.2, ~744B MoE) do not fit in 24 GB VRAM; they need CPU/GPU hybrid MoE engines that keep
attention and hot experts on the GPU and stream or offload the rest from RAM/NVMe. Candidates:
KTransformers (Apache-2.0), colibrì (expert streaming), llama.cpp with `--override-tensor` expert offload.

**Goal.** Measure, don't guess. Pick the best engine per lane on this hardware, then wire the judge
lane to port 8003 and the deep lane to 8004.

**Fit math first (do this before downloading anything).** Pablo has 24 GB VRAM + 64 GB RAM.
A ~150B MoE at Q4 is roughly 85-90 GB of weights, which does NOT fit in RAM+VRAM with the OS and the
router. Work out the size of each candidate quant, then pick one that fits with ≥8 GB RAM to spare, or
plan explicitly to stream experts from NVMe (and expect it to be slow). Write the math in
`docs/decisions.md`. Include at least one smaller candidate from a DIFFERENT model family than the
coder (a judge from the same family shares its blind spots) that runs fully on the GPU while the coder
is stopped. Choose the judge by `pablo eval-judge` accuracy first, speed second.

**Don't touch** `src/pablo/orchestrator.py`, `src/pablo/router.py`, or `src/pablo/context*.py`; Claude
is changing those in M2 at the same time. Judge work belongs in `judge.py`, config, scripts, and docs.
Run `git pull --rebase origin main` before each commit.

**Steps**
0. Before changing anything: `uv run pablo eval-judge --write` with the current setup (coder acting
   as judge) and commit it. That's the baseline every candidate must beat.
1. Confirm current releases, licenses, and supported models for each engine. Record versions.
2. Download the judge model in a quant the engines support (archive it with checksums). Only start the
   deep model after the judge works: it is big; check free disk first and tell me the size.
3. Write `scripts/bench.py`: same 5 prompts (2 judge-style, 2 code, 1 long-context 8k), measures
   load time, time to first token, decode tokens/s, peak VRAM (nvidia-smi polling), peak RSS.
   Output a markdown table appended to `docs/benchmarks.md`.
4. Bench the judge model on each engine that supports it. Stop other lanes first so VRAM is clean.
5. Choose an engine per lane; record why in `docs/decisions.md`. Serve judge on 127.0.0.1:8003,
   point `pablo/judge` in `config/litellm.yaml` at it.
6. Judge quality check: `uv run pablo eval-judge --write` scores the judge on the 10 fixtures in
   `evals/judge` (5 good, 5 bad; four of the bad ones pass their tests, so the judge must read the
   diff). Tune `JUDGE_PROMPT` in judge.py until accuracy is at least 9/10, re-running each time.
   Add 5 more cases from real M1 runs to `evals/judge/cases.yaml` (they feed M7 evals).
7. Deep lane (GLM-5.2, ~744B): only do the fit math and disk-size check this session and write it to `docs/decisions.md`. Don't download it until the judge is done and committed; it is several hundred GB and will only run by streaming from NVMe.

**Acceptance**
- `docs/benchmarks.md` has engine-vs-engine tables for the judge (and deep, if done).
- Judge separates good from broken diffs on the 10 fixtures (report the scores).
- `pablo run` uses the real judge. Tests, ruff, mypy pass. BUILD_PLAN M3 marked done.
