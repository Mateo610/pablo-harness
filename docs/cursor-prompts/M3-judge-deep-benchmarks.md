# M3 · Judge and deep engines, measured

**Context.** M1 runs router + coder on llama.cpp. The judge (DeepSeek V4 Flash REAP ~150B MoE) and the
deep lane (GLM-5.2, ~744B MoE) do not fit in 24 GB VRAM; they need CPU/GPU hybrid MoE engines that keep
attention and hot experts on the GPU and stream or offload the rest from RAM/NVMe. Candidates:
KTransformers (Apache-2.0), colibrì (expert streaming), llama.cpp with `--override-tensor` expert offload.

**Goal.** Measure, don't guess. Pick the best engine per lane on this hardware, then wire the judge
lane to port 8003 and the deep lane to 8004.

**Steps**
1. Confirm current releases, licenses, and supported models for each engine. Record versions.
2. Download the judge model in a quant the engines support (archive it with checksums). Only start the
   deep model after the judge works: it is big; check free disk first and tell me the size.
3. Write `scripts/bench.py`: same 5 prompts (2 judge-style, 2 code, 1 long-context 8k), measures
   load time, time to first token, decode tokens/s, peak VRAM (nvidia-smi polling), peak RSS.
   Output a markdown table appended to `docs/benchmarks.md`.
4. Bench the judge model on each engine that supports it. Stop other lanes first so VRAM is clean.
5. Choose an engine per lane; record why in `docs/decisions.md`. Serve judge on 127.0.0.1:8003,
   point `pablo/judge` in `config/litellm.yaml` at it.
6. Judge quality check: run 10 real M1 diffs (5 good, 5 deliberately broken) through the judge and
   record certainty. Tune `JUDGE_PROMPT` until broken diffs score below 0.75 and good ones above.
   Save these 10 as fixtures for M7 evals.
7. Repeat steps 3–5 for the deep lane on 8004 (fine if it's slow; it runs overnight).

**Acceptance**
- `docs/benchmarks.md` has engine-vs-engine tables for the judge (and deep, if done).
- Judge separates good from broken diffs on the 10 fixtures (report the scores).
- `pablo run` uses the real judge. Tests, ruff, mypy pass. BUILD_PLAN M3 marked done.
