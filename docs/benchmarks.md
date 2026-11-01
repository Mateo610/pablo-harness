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

`--memory 4g` is set and is not enforced inside the guest. `test_memory_limit` fails: an 8 GB allocation succeeds. Before the process and CPU pins, all three limits were lost: the 8 GB allocation succeeded, 400 `sleep` processes started, and `sched_getaffinity` reported 16 CPUs.

`runsc flags` has no total-memory option. Podman 4.9.3 rejects `--ulimit as` (`invalid ulimit type: as`). `--systemd-cgroup` on the system bus returns "Interactive authentication required." The same flag with the session bus on `DBUS_SYSTEM_BUS_ADDRESS` then fails with `open /sys/fs/cgroup/cgroup.subtree_control: permission denied`. The user slice already lists controllers `cpu memory pids`.

`--ulimit nproc=256` stops the 256th child (`BlockingIOError`, 255 started). `taskset -c 12-15` makes the guest report affinity `[0, 1, 2, 3]` and `cpu_count` 4.

The sentry shares that address space. `ulimit -v` of 4 GiB kills `uv run pytest` (it cannot write its cache). 7 GiB, which is the 4 GiB guest cap plus 3 GiB for runsc 20261026.0, lets `uv run pytest` and a 3 GiB bytearray through. A 4 GiB bytearray and an 8 GiB bytearray are both killed (exit 139). `test_memory_limit`, `test_pids_limit`, and `test_cpu_limit` pass. Removing the `pablo-check:py311` tag makes the next check fail with `sandbox unavailable` and nothing runs on the host. The tag was restored.

## 2026-11-01 · M4b gpt-oss-120b vs coder

`pablo compare --lanes coder,deep` on the six tasks in `evals/deep/tasks.yaml`. Deep was gpt-oss-120b MXFP4. Nothing was approved. Wall-clock is the pair, not a score. Harness checks (`pytest` and `mypy` inside runsc) fail at the baseline for both lanes, so the two design pairs are scored on the note. Ledger checks are `uv run pytest -q` and the ledger image is green, so those checks count when they exercise the real code.

| Task | Coder | Deep | Deep vs coder | Wall |
|---|---|---|---|---|
| design-check-limits | `df3cf98812` | `a40d2b608a` | same | 186.6 s |
| design-deep-vs-coder | `87ce5bd27c` | `dbf803a2a3` | better | 201.7 s |
| refactor-format | `136d8811de` | `0d73a95d29` | same | 408.3 s |
| refactor-dates | `6507380fea` | `ad37fe87a9` | same | 198.9 s |
| bug-monthly-amount | `b2fc663765` | `f8e91c5ece` | same | 165.7 s |
| bug-load-line | `a1ced0854a` | `f1138054e4` | worse | 837.7 s |

Total wall 1999 s. Score: 1 better, 1 worse, 4 same. That is not a keep.

design-check-limits: both notes invent a mechanism this machine does not use (systemd-run, cpulimit, a 2 GB cap). The real caps are `nproc`, `taskset -c 12-15`, and `RLIMIT_AS`. design-deep-vs-coder: both notes stay inside `config/lanes.yaml`. Deep lists both keyword lists plus `max_tokens` 32768 and `timeout_s` 3600; coder omits the coder keywords. refactor-format: both moved `format_cents` and re-exported it from `money.py`, so the existing tests passed, and neither updated `tests/test_ledger.py`. refactor-dates and bug-monthly-amount: neither lane produced a diff. bug-load-line: coder's search missed `ledger/entries.py`. Deep added `sitecustomize.py` that patches `ledger.load_line` and a test that imports that patch. `ledger.entries.load_line` is unchanged, so the green check does not count.

## 2026-11-01 · M4b Qwen3.5-122B-A10B fit

Unsloth `Qwen3.5-122B-A10B-UD-Q3_K_XL`, revision `51eab4d59d53f573fb9206cb3ce613f1d0aa392b`, three shards, 53.06 GiB. SHA256 matches the LFS oids. The first download of shard 3 had the right length and the wrong hash; it was deleted and fetched again. Router stayed on CPU. Coder was unloaded first (VRAM 807 MiB).

48 layers, 256 experts, 8 active. `--n-cpu-moe 25` asks CUDA for 28648 MiB and fails. N=32 loads the weights and then fails on the 1536 MiB 64k KV cache. N=34 with `--parallel 1` and `-c 65536` loads. `--reasoning on` puts the trace in `reasoning_content` and the answer in `content`.

| | |
|---|---|
| VRAM at load and after 48 decoded tokens | 23423 / 24576 MiB (702 MiB free) |
| Process RSS | 39.5 GiB |
| MemAvailable, router up | 55.7 GiB |
| Decode, 48 tokens, short prompt | 21.5 tok/s |
| Decode, 168 tokens, reasoning on | 21.0 tok/s |

GGUF metadata sampling is temperature 0.6, top_p 0.95, top_k 20. The lane sent 0.6 / 0.95 for this bake-off. `--top-k 20` was on the server because the harness does not send top_k. After the score below, the lane and the swapper went back to gpt-oss at temperature 1.0 and top_p 1.0.

## 2026-11-01 · M4b Qwen3.5-122B-A10B vs coder

Same six tasks, nothing approved. Deep was the Qwen fit above. Harness checks are still red for both lanes, so the design pairs are scored on the note. Ledger checks count when they hit the real code.

| Task | Coder | Deep | Deep vs coder | Wall |
|---|---|---|---|---|
| design-check-limits | `fe2ee93c5f` | `b3529e431d` | same | 136.4 s |
| design-deep-vs-coder | `734240db69` | `bedc1d478b` | better | 132.0 s |
| refactor-format | `5b872d34a1` | `c4d72801ae` | worse | 110.7 s |
| refactor-dates | `f0b61c4df4` | `6a14c89d68` | same | 99.3 s |
| bug-monthly-amount | `81471af796` | `4d72807e1f` | same | 159.8 s |
| bug-load-line | `601ce49c40` | `bd756d1922` | same | 181.5 s |

Total wall 820 s. Score: 1 better, 1 worse, 4 same. Not a keep. Deep stays off.

design-check-limits: both notes still invent a 2 GB systemd cap. design-deep-vs-coder: Qwen lists both keyword lists from `config/lanes.yaml`; the coder invents a reason about block diffs. refactor-format: the coder moved `format_cents` and re-exported it, so the tests passed; Qwen removed it from `money.py` without updating the test import, and the tests failed. refactor-dates and bug-load-line: neither lane produced a diff. bug-monthly-amount: the coder added a new root `ledger.py` and left `ledger/report.py` alone; Qwen produced no diff.

### Judge eval · pablo/judge

Accuracy 94% at threshold 0.75 · mean certainty good 0.96, bad 0.37

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.80 | yes |
| good-fix-double-spaces | good | pass | 0.90 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.30 | yes |
| bad-deleted-test | bad | pass | 0.90 | NO |
| bad-hardcoded | bad | pass | 0.30 | yes |
| bad-shell-call | bad | pass | 0.30 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| bad-patched-not-fixed | bad | pass | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.30 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.30 | yes |

### Judge eval · pablo/judge

Accuracy 94% at threshold 0.75 · mean certainty good 0.90, bad 0.29

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
| bad-patched-not-fixed | bad | pass | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.30 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.20 | yes |

### Judge eval · pablo/judge

Accuracy 94% at threshold 0.75 · mean certainty good 0.97, bad 0.36

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.80 | yes |
| good-fix-double-spaces | good | pass | 1.00 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.30 | yes |
| bad-deleted-test | bad | pass | 0.90 | NO |
| bad-hardcoded | bad | pass | 0.30 | yes |
| bad-shell-call | bad | pass | 0.30 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| bad-patched-not-fixed | bad | pass | 0.30 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.30 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.20 | yes |

### Judge eval · pablo/judge

Accuracy 100% at threshold 0.75 · mean certainty good 0.96, bad 0.26

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| good-slugify | good | pass | 0.80 | yes |
| good-fix-double-spaces | good | pass | 0.90 | yes |
| good-docstrings | good | pass | 1.00 | yes |
| good-validate-limit | good | pass | 1.00 | yes |
| good-refactor-same-behavior | good | pass | 1.00 | yes |
| bad-weakened-test | bad | pass | 0.30 | yes |
| bad-deleted-test | bad | pass | 0.30 | yes |
| bad-hardcoded | bad | pass | 0.20 | yes |
| bad-shell-call | bad | pass | 0.30 | yes |
| bad-breaks-tests | bad | fail | 0.30 | yes |
| bad-patched-not-fixed | bad | pass | 0.20 | yes |
| good-m1-mul | good | pass | 1.00 | yes |
| bad-m1-sum-fails-test | bad | fail | 0.20 | yes |
| good-m1-sub | good | pass | 1.00 | yes |
| bad-m1-unindented | bad | fail | 0.30 | yes |
| bad-m1-asserts-sum | bad | pass | 0.20 | yes |

### Judge eval · pablo/judge · holdout

Accuracy 90% at threshold 0.75 · mean certainty good 0.82, bad 0.24

| case | expect | checks | certainty | right? |
|---|---|---|---|---|
| ho-good-count-lines | good | pass | 0.30 | NO |
| ho-good-word-boundary | good | pass | 0.80 | yes |
| ho-good-reverse-words | good | pass | 1.00 | yes |
| ho-good-all-exports | good | pass | 1.00 | yes |
| ho-good-strip-before-ellipsis | good | pass | 1.00 | yes |
| ho-bad-opposite-of-task | bad | pass | 0.20 | yes |
| ho-bad-skipped-test | bad | pass | 0.20 | yes |
| ho-bad-wrong-function | bad | pass | 0.30 | yes |
| ho-bad-cache-key | bad | pass | 0.30 | yes |
| ho-bad-empty-tests | bad | pass | 0.20 | yes |

## 2026-11-01 · M4c judge

The three main tables above the 100% table are tuning passes on the same prompt, not a final score. The frozen prompt is the 100% table: 16/16, mean certainty good 0.96 and bad 0.26. `bad-patched-not-fixed` is 0.20. Holdout was scored once after that and not tuned: 9/10. `ho-good-count-lines` is the miss, at 0.30.

Run `f1138054e4` (gpt-oss, bug-load-line, the sitecustomize patch) was certainty 0.90. Rescored with this prompt, checks still green, it is 0.30. The summary names the patch instead of calling the diff a fix.

## 2026-11-01 · M4c sandbox baseline

A scratch copy of this repo in `pablo-check-pablo-harness-main:bcbd41e67aa1` failed pytest because `python:3.11-slim` has no `git` (`FileNotFoundError: git` at setup). After git was installed in the base and the harness image rebuilt, the same scratch pytest passed: 122 passed, 10 skipped, 76 s inside runsc.

`python -m mypy .` still fails: 16 errors in `tests/` and `scripts/bench.py`. `python -m mypy` follows `[tool.mypy] packages = ["pablo"]` and is clean (24 files, 4.4 s). That is the command in `.pablo/checks.yaml`.

## 2026-11-01 · M4c no-diff runs from both bake-offs

Ten of the 24 bake-off runs have an empty diff. None hit `max_tokens` (coder cap 4096, deep cap 32768) and none timed out. Where a repair ran, the detail is the error that left the diff empty.

| Bake-off | Task | Run | Lane | Why there is no diff | Completion tokens |
|---|---|---|---|---|---|
| gpt-oss | refactor-dates | `6507380fea` | coder | replied without edit blocks | 141 |
| gpt-oss | refactor-dates | `ad37fe87a9` | deep | edits didn't apply (`refusing path outside the repo: ''`), then the repair replied without edit blocks | 880 |
| gpt-oss | bug-monthly-amount | `b2fc663765` | coder | SEARCH missed (`src/ledger.py`, `def total(lines):`) | 437 |
| gpt-oss | bug-monthly-amount | `f8e91c5ece` | deep | SEARCH missed (`report.py`, `def monthly(month: str) -> str:`), then the repair replied without edit blocks | 1161 |
| gpt-oss | bug-load-line | `a1ced0854a` | coder | SEARCH missed (`path/to/ledger.py`, `def load_line(...)`) | 1698 |
| Qwen | refactor-dates | `f0b61c4df4` | coder | edits didn't apply (empty SEARCH on `ledger/dates.py`, which already has content) | 1084 |
| Qwen | refactor-dates | `6a14c89d68` | deep | replied without edit blocks (a tool-call shaped reply) | 576 |
| Qwen | bug-monthly-amount | `4d72807e1f` | deep | SEARCH missed (`report.py`, `def monthly(...)`) | 518 |
| Qwen | bug-load-line | `601ce49c40` | coder | SEARCH missed (`tests/test_ledger.py`, `def test_parse_and_format():`) | 497 |
| Qwen | bug-load-line | `bd756d1922` | deep | SEARCH missed (`ledger.py`, `def load_line(...)`) | 1086 |

## 2026-11-01 · M4c context file lists, no models

`context_files` for the six tasks in `evals/deep/tasks.yaml`, coder budget 10500 and deep budget 24000. Harness tasks use this repo. Ledger tasks use `~/pablo-sandbox/ledger`, which is not a git repo, so the file walk includes `.venv`. Every required code target is in the coder list. The first eight files:

| Task | Lane | Files |
|---|---|---|
| design-check-limits | coder | 6: `docs/benchmarks.md`, `docs/decisions.md`, `docs/cursor-prompts/M5-sandbox.md`, `tests/test_context.py`, `docs/cursor-prompts/M4b-deep-lane.md`, `src/pablo/sandbox.py` |
| design-check-limits | deep | 60, starting with the same six, then `docs/cursor-prompts/M0-pablo-baseline.md`, `docs/cursor-prompts/M4c-deep-rerun.md` |
| design-deep-vs-coder | coder | 6: `config/lanes.yaml`, `docs/cursor-prompts/M4b-deep-lane.md`, `docs/benchmarks.md`, `docs/cursor-prompts/M1-gateway-and-first-lanes.md`, `docs/cursor-prompts/M4c-deep-rerun.md`, `docs/decisions.md` |
| design-deep-vs-coder | deep | 60, starting with the same six, then `docs/cursor-prompts/M5-sandbox.md`, `docs/cursor-prompts/M4-lane-swapping-resources.md` |
| refactor-format | coder | 6: `ledger/money.py`, `ledger/entries.py`, `ledger/report.py`, `tests/test_ledger.py`, then two files under `.venv` |
| refactor-format | deep | 60, same start, then more `.venv` |
| refactor-dates | coder | 6: `ledger/entries.py`, `ledger/report.py`, one `.venv` file, `ledger/__init__.py`, `tests/test_ledger.py`, one `.venv` file |
| refactor-dates | deep | 60, same start, then more `.venv` |
| bug-monthly-amount | coder | 6: `ledger/report.py`, `ledger/entries.py`, `ledger/money.py`, one `.venv` file, `ledger/__init__.py`, `tests/test_ledger.py` |
| bug-monthly-amount | deep | 60, same start, then more `.venv` |
| bug-load-line | coder | 6: `ledger/entries.py`, `tests/test_ledger.py`, then four files under `.venv` |
| bug-load-line | deep | 60, same start, then more `.venv` |

Coder targets that had to be present: refactor-dates `entries.py` and `report.py`; bug-monthly-amount `report.py` and `entries.py`; bug-load-line `entries.py`; refactor-format `money.py` and `tests/test_ledger.py`. All of them are.

## 2026-11-01 · M4c canary · bug-load-line

`pablo compare --lanes coder,deep` on the ledger repo. Deep was gpt-oss-120b. Wall for the pair: 326 s. Deep was thinking for about 4 minutes and returned before the 3600 s timeout. Both swaps finished and the judge ran. Nothing was approved. The `context` events match the lists above: coder 6 files and deep 60, both starting `ledger/entries.py`, `tests/test_ledger.py`, then `.venv`.

| Task | Coder | Deep | Deep vs coder | Coder judge | Deep judge | Wall |
|---|---|---|---|---|---|---|
| bug-load-line | `4435d2a799` | `5fc37d0a5a` | better | 0.30 | 1.00 | 326 s |

Coder `ledger/entries.py`: `dollars, payee = rest.rsplit(",", 1)` swaps the two fields, so `parse_dollars` receives `Cafe, Main St` and both the new test and `test_total_and_load_line` fail. That does not fix `load_line`. Deep, same file: `day = parts[0]`, `dollars = parts[-1]`, `payee = ", ".join(parts[1:-1])`, plus `tests/test_load_line_commas.py` for `2026-11-02, Cafe, Main St, $1.50`. The real function changes, and the ledger pytest passes (3 passed). The hand score is from those lines. The judge numbers sit beside it: 0.30 because the coder's checks regressed, 1.00 on the deep diff. Keep or drop is not decided from this one pair.
