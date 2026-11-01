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

## 2026-11-01 · DeepSeek V4 Flash REAP K128 does not load

llama.cpp `b12031` with `--cpu-moe -ngl 99` and colibrì v2.1.0 (`704a3f6`) both refuse the 46.98 GiB GGUF. Layer 3's router is 128 experts wide while both engines expect 256. No tokens were produced. The larger quants do not fit the spare-RAM rule, so they were not downloaded.
