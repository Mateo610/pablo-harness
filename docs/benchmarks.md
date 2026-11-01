# Benchmarks

Measured on Pablo (RTX 3090 24 GB, 64 GB DDR4, WD SN7100 2 TB). Append, never overwrite.

## 2026-11-01 · M1 coder and router

llama.cpp `b12031` (commit `060b2fe`), CUDA sm_86. Context 16384. Thinking off (`--reasoning off`).

| Lane | Weights | Where | Load | Prompt tok/s | Gen tok/s | Time to first token |
|---|---|---|---|---|---|---|
| coder | Qwen3.6-35B-A3B UD-Q4_K_S | GPU, `-ngl 99`, `-ctk q8_0 -ctv q8_0`, `--n-cpu-moe 2` | 9 s | 127 | 121 | 0.17 s |
| router | Qwen3.5-4B Q4_K_M | CPU, `-ngl 0` | 3 s | 60 | 12 | 0.54 s |

VRAM during a coder generation, router resident on CPU: used 20426 MiB, free 3696 MiB (headroom 3.6 GB). N was not raised past 2.

Scratch repo `~/pablo-sandbox/hello`, checks `uv run pytest -q`.

| Task | Result | Checks | Judge certainty |
|---|---|---|---|
| add `mul(a, b)` with a test | pass | passed | 1.00 |
| fix `mul` (returned a sum) | pass | passed | 1.00 |
| add `sub(a, b)` with a test | pass | passed | 1.00 |

The coder often undercounts `@@` hunk lengths and inserts a blank context line the file does not have. The harness recounts hunks and, if `git apply` fails, retries once without that blank line.

## 2026-11-01 · M3 baseline, coder acting as judge

`pablo/judge` still points at the coder (Qwen3.6-35B-A3B on llama.cpp `b12031`). Threshold 0.75. It scored 9/10 and missed `bad-deleted-test` (certainty 1.00 while the checks passed). Later judge candidates have to beat this 90%.

### Judge eval · pablo/judge

Accuracy 90% at threshold 0.75 · mean certainty good 1.00, bad 0.20

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 1.00 | yes |
| good-fix-double-spaces | good | pass | 1.00 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.00 | yes |
| bad-deleted-test | bad | pass | 1.00 | NO |
| bad-hardcoded | bad | pass | 0.00 | yes |
| bad-shell-call | bad | pass | 0.00 | yes |
| bad-breaks-tests | bad | fail | 0.00 | yes |

### Bench · llama.cpp b12031 · Mistral-Small-3.2-24B heretic Q5_K_M

Load 6.0 s. Peaks over the whole run: VRAM 18439 MiB, RSS 16317 MiB.

| prompt | kind | prompt tokens | ttft s | decode tok/s |
|---|---|---|---|---|
| judge-good-diff | judge | 638 | 0.37 | 45.7 |
| judge-deleted-test | judge | 666 | 0.37 | 45.6 |
| code-mul | code | 584 | 0.33 | 45.4 |
| code-word-count | code | 584 | 0.33 | 46.8 |
| long-context-8k | long | 13782 | 8.25 | 38.7 |

Cold cache. The four short prompts decode at about 46 tok/s. The long prompt is 13782 tokens. The live service uses `-ngl 0` so the coder can keep the GPU; run `350523f862` decoded at 2.42 tok/s on that CPU path. The tables below are prompt-tuning passes. The last one is the prompt in `judge.py`.

### Judge eval · pablo/judge

Accuracy 73% at threshold 0.75 · mean certainty good 0.97, bad 0.61

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.90 | yes |
| good-fix-double-spaces | good | pass | 0.90 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.80 | NO |
| bad-deleted-test | bad | pass | 1.00 | NO |
| bad-hardcoded | bad | pass | 0.50 | yes |
| bad-shell-call | bad | pass | 0.90 | NO |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.30 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.80 | NO |

### Judge eval · pablo/judge

Accuracy 87% at threshold 0.75 · mean certainty good 0.97, bad 0.38

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.80 | yes |
| good-fix-double-spaces | good | pass | 1.00 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.80 | NO |
| bad-deleted-test | bad | pass | 0.80 | NO |
| bad-hardcoded | bad | pass | 0.30 | yes |
| bad-shell-call | bad | pass | 0.30 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.10 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.10 | yes |

### Judge eval · pablo/judge

Accuracy 87% at threshold 0.75 · mean certainty good 0.74, bad 0.21

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.20 | NO |
| good-fix-double-spaces | good | pass | 0.20 | NO |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 0.80 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.20 | yes |
| bad-deleted-test | bad | pass | 0.20 | yes |
| bad-hardcoded | bad | pass | 0.20 | yes |
| bad-shell-call | bad | pass | 0.20 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.20 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.20 | yes |
| bad-m1-asserts-sum | bad | pass | 0.20 | yes |

### Judge eval · pablo/judge

Accuracy 93% at threshold 0.75 · mean certainty good 0.90, bad 0.30

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.30 | NO |
| good-fix-double-spaces | good | pass | 1.00 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.30 | yes |
| bad-deleted-test | bad | pass | 0.30 | yes |
| bad-hardcoded | bad | pass | 0.30 | yes |
| bad-shell-call | bad | pass | 0.30 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.30 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.30 | yes |

### Judge eval · pablo/judge

Accuracy 100% at threshold 0.75 · mean certainty good 0.96, bad 0.28

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.80 | yes |
| good-fix-double-spaces | good | pass | 0.90 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.30 | yes |
| bad-deleted-test | bad | pass | 0.30 | yes |
| bad-hardcoded | bad | pass | 0.30 | yes |
| bad-shell-call | bad | pass | 0.30 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.20 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.20 | yes |

## 2026-11-01 · M4 lane swaps

llama-swap v269 on 127.0.0.1:8100. Router resident on CPU. Coder and judge swap on the GPU; the judge uses `-ngl 99`. `scripts/soak.py` ran 20 coder runs on `~/pablo-sandbox/hello`, cycling add `mul`, fix `mul` (it returned a sum), and add `sub`. Each task was staged to the file state it had in M1, then the original `hello.py` and `test_hello.py` were restored. Nothing was approved.

0 OOM, 0 engine crashes, 0 of 20 runs needed a repair pass. All 20 finished `ready`. After runs 5, 10, 15, and 20 a short `pablo/router` completion answered `ok` and `/running` still listed the router as ready. Coder and judge were never ready at the same time.

| Direction | Swaps | Min | Median | Max |
|---|---|---|---|---|
| coder → judge | 20 | 3.7 s | 3.7 s | 4.0 s |
| judge → coder | 19 | 5.0 s | 5.1 s | 5.3 s |

The soak ends on the judge, so the return swap is one short of 20. Both directions are under 15 s, so `--mlock` and `--no-mmap` were not tried. Wall clock for the 20 runs was 240 s.

`curl 127.0.0.1:8710/api/resources` after the soak, judge resident: RTX 3090, VRAM 19591/24576 MiB, RAM 9643/64227 MiB, loaded `judge, router`.

## 2026-11-01 · DeepSeek V4 Flash REAP K128 does not load

llama.cpp `b12031` with `--cpu-moe -ngl 99` and colibrì v2.1.0 (`704a3f6`) both refuse the 46.98 GiB GGUF. Layer 3's router is 128 experts wide while both engines expect 256. No tokens were produced. The larger quants do not fit the spare-RAM rule, so they were not downloaded.

## 2026-11-01 · M4b gpt-oss-120b fit

llama.cpp `b12031` (commit `060b2fe`). Weights `gpt-oss-120b-MXFP4.gguf`, 59.0 GiB, ggml-org rev `b5c939de8f754692c1647ca79fbf85e8c1e70f8a`. Router resident on CPU. Coder and judge unloaded first: VRAM fell to 822 MiB and the router still answered. The Eagle3 drafts in that directory were not loaded.

Serving flags: `-ngl 99 --n-cpu-moe 25 -c 65536 --flash-attn on --jinja --reasoning on --reasoning-effort high`. Temperature 1.0, top_p 1.0.

N=24 peaked at 22111 MiB and then failed: the 64k KV cache is 2304 MiB and `cudaMalloc` returned out of memory. N=26 loads at 21944 MiB. N=25 is the one in the swapper.

Cold load 26.5 s, warm load 23.6 s, both to HTTP 200. The cold start followed a full read of the archive copy so the weights were not sitting in the page cache; there is no passwordless sudo to drop caches. Both are far under llama-swap's 180 s `healthCheckTimeout`, so that timeout stayed. Process RSS was about 38 GiB, almost all of it the mmap'd file. `MemAvailable` stayed at 55–56 GiB through the 24k bench. With deep resident, `/api/resources` reported VRAM 23553/24576 MiB, RAM 6444/64227 MiB, loaded `deep, router`.

| prompt | prompt tokens | ttft s | decode tok/s |
|---|---|---|---|
| ~0 | 74 | 2.11 | 25.3 |
| 8k | 8259 | 10.60 | 25.7 |
| 24k | 25168 | 21.48 | 25.8 |

48 decoded tokens, `cache_prompt` false. Decode stays near 25 tok/s out to 24k. VRAM peak across this bench was 23570 MiB, 565 MiB free.

A short question through the gateway (`pablo/deep` on the swapper) returned `content` `391` and a separate `reasoning_content` (116 characters). Harmony channel text did not land in `content`, so `--reasoning-format` was left at its default.

| Direction | Time |
|---|---|
| coder → deep | 27.4 s |
| deep → judge | 9.8 s |

One sample each: from the moment the previous GPU model left `ready` until the next was `ready`. The router stayed `ready`. No two GPU lanes were ready together. The coder was loaded again afterward and is the resident model (20316 MiB, 3809 MiB free).

## 2026-11-01 · M5 check sandbox

Rootless Podman 4.9.3, gVisor `runsc` 20261026.0 via `~/pablo-data/bin/runsc-ignore-cgroups`. Image `pablo-check-hello:08ffbfa46841`. Command `uv run pytest -q`. `lanes.yaml` stayed on `kind: container`.

LocalSandbox on `~/pablo-sandbox/hello` (its existing `.venv`): 0.18 s, then 0.14 s. ContainerSandbox on a scratch copy, which is how a real check runs: 1.40 s the first time, then 0.64 s and 0.63 s. Warm gVisor startup adds about half a second.

Three coder runs (`--lane coder`), nothing approved. Each verdict is `podman/runsc pablo-check-hello:08ffbfa46841`.

| Task | Run | Check | Wall | Judge |
|---|---|---|---|---|
| add `div` | `083ad31c3c` | passed 0.63 s | 13.0 s | 1.00 |
| add `clamp` | `d132eb5f13` | passed 0.63 s | 12.2 s | 0.90 |
| add `abs_diff` | `522a463cd0` | passed 0.63 s | 11.9 s | 1.00 |

`uv run pablo eval-judge --set main` scored 15/15 again. Accuracy 100% at 0.75, mean certainty good 0.96 and bad 0.28, and every case matched the table already recorded above.

`--memory 4g` is set and is not enforced inside the guest. `test_memory_limit` fails: an 8 GB allocation succeeds. That is the `--ignore-cgroups` cost in `docs/decisions.md`. The other seven container tests pass. Removing the `pablo-check:py311` tag makes the next check fail with `sandbox unavailable` and nothing runs on the host. The tag was restored.
