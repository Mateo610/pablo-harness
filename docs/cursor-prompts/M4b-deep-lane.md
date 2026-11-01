# M4b · Pick and serve the deep lane

**Context.** M3 ruled out GLM-5.2: the smallest GGUF is about 217 GB, so it can only stream from
the NVMe, at well under 1 tok/s (about 12 GB of experts per token). That's one answer a night, which
is not a lane. The DeepSeek V4 Flash REAP doesn't load (128 vs 256 experts). M4 left deep out of the
swapper. The fit rule from `docs/decisions.md` stays: about 21 GB on the GPU (coder and judge
swapped out), at most about 43 GB of weights in RAM, at least 8 GB of RAM left free with the router up.

**Decision (made with Claude, 2026-11-01).** Bake off two Apache-2.0 MoE models. Keep the deep lane
only if the winner beats the coder on hard tasks; otherwise deep stays off and hard tasks go to the
coder with more time.

| | gpt-oss-120b (pick) | Qwen3.5-122B-A10B (challenger) |
|---|---|---|
| Weights | MXFP4 GGUF, about 61 GB, native precision | UD-Q3_K_XL (3-bit), about 60 GB total memory per Unsloth |
| Active per token | about 5.1B | about 10B |
| Expected on DDR4 | about 12–20 tok/s (estimate) | about half of that (estimate) |
| Source | `ggml-org/gpt-oss-120b-GGUF` | `unsloth/Qwen3.5-122B-A10B-GGUF` |
| Notes | reasoning effort via `--chat-template-kwargs '{"reasoning_effort":"high"}'`; temp 1.0, top-p 1.0 | Q4 is about 70 GB and does not fit; check the exact Q3 file size first; must be ≤ 58 GB |

Not chosen: Nemotron 3 Super 120B-A12B (NVIDIA Open Model License, not Apache; ownership rule),
GLM-5.2 streaming (above). Confirm each repo's license, revision, and file sizes before downloading.

**Steps**
1. `df -h ~/pablo-data` first. Download gpt-oss-120b to `~/pablo-data/models/gpt-oss-120b/` (Matt runs
   the download: it's over 20 GB). Record the revision. Archive with `scripts/archive-model.sh`.
2. Fit it: `llama-server -m <gguf> -ngl 99 --n-cpu-moe N -c 32768 --flash-attn on --jinja
   --chat-template-kwargs '{"reasoning_effort":"high"}' --host 127.0.0.1`. Start at N=24 and lower N
   until VRAM peaks at about 22.5 GB with the coder and judge stopped. With the router up, `free -g`
   must still show at least 8 GB available. Record N, VRAM peak, RAM, load time (cold and warm), and
   tok/s at 0, 8k, and 24k context in `docs/benchmarks.md`.
3. Check the reply shape through the gateway: the answer must land in `message.content`, with the
   reasoning in `reasoning_content` (the harness reads only `content`). If llama.cpp puts the
   harmony channels into `content`, fix it in the server flags (`--reasoning-format`), not in Python.
4. Add `deep` to `config/llama-swap.yaml` as a third member of the `gpu` group (`swap: true`). Point
   `pablo/deep` in `config/litellm.yaml` at the swapper on :8100. `ttl: 0`. The coder stays the default
   resident. Measure coder → deep and deep → judge swap times.
5. Bake-off. Write 6 hard tasks in `evals/deep/tasks.yaml` (2 architecture or design questions about
   this repo, 2 multi-file refactors on `~/pablo-sandbox`, 2 bugs that need reasoning across files).
   Run `pablo compare "<task>" --lanes coder,deep` for each. Score each pair by hand (better / same /
   worse) and record the table plus wall-clock time. Then do the same for Qwen3.5-122B-A10B only if
   gpt-oss loses or ties on 4 or more of the 6.
6. Overnight check: queue 3 tasks with `pablo queue add` (default lane deep), let the scheduler run
   them in the 01:00–06:30 window, and confirm the morning brief lists them.

**Acceptance**
- `pablo doctor` and `/api/resources` show `deep` loaded after a deep run; no OOM; the router never
  evicted.
- `docs/decisions.md` records the winner (or "deep off") with the bake-off table. THIRD_PARTY.md has the
  weights row with license and revision. `config/lanes.yaml` deep comment names the model.
- BUILD_PLAN gets an M4b row marked done.
