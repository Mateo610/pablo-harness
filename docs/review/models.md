# Models

## What it does

| Role | Model | How we run it |
|---|---|---|
| Router | Qwen3.5-4B Q4_K_M | CPU, always loaded |
| Base (coder) | Qwen3.6-35B-A3B UD-Q4_K_S | GPU, `--reasoning off`, 16k window (~10.5k for context), ~121 tok/s. Extended thinking is the same weights, `--reasoning on`, 32k window, prompt budget 10500 |
| Judge | Qwen3.8-27B dense UD-Q4_K_M | GPU, `--reasoning off`, threshold 0.75 |
| Best (deep) | gpt-oss-120b MXFP4 | experts on CPU (`--n-cpu-moe 25`), 64k window, reasoning on, ~25 tok/s |
| No guardrails (advisor) | Mistral Small 3.2 24B heretic Q5_K_M | GPU, talks only (builds only in unfiltered projects) |
| Voice | whisper.cpp small.en (CPU), Qwen3-TTS 0.6B (GPU) | |

## What we know

- **[measured]** Base alone on the 36-task suite: 79–88% across baselines; latest trio 86% ± 3%
  (`5fe0997`). Best alone: 34/36 (94%) in one run (`a30df2a`), wall 12,917 s.
- **[measured]** Hybrid with the old judge: 92% ± 3% (`5fe0997`). With the Qwen judge, one clean
  run: 32/36, false ready 4 (`3cb60a6`, run 1). Tonight's run gives the real number.
- **[measured]** The judge bake-off on 143 real suite diffs picked Qwen3.8-27B dense. Stock
  Mistral would have flagged 55 of 125 good runs.
- **[measured]** At first, gpt-oss-120b didn't beat the coder (M4b: 1 better, 1 worse, 4 same).
  After the context fix it scored 34/36. The first result was about context, not the model.
- **[measured]** Hybrid chat (this coder, reasoning off) on 22 labeled messages at `2563ea6`:
  typed 14/22, spoken 16/22. Plan 6/6 both ways. Spoken run 8/8; typed run 5/8 (3 became plans).
  Talk 3/8 typed and 2/8 spoken: a question usually starts a run.
- **[measured]** Hybrid plan mode on 5 small tools from an empty project (`90319fc`): accepted
  1/5, planned 5/5, finished 5/5, mean steps 3.8, wall 1059 s. Seven approvals were clean but
  the judge was unsure. None rejected. No project errored.
- **[measured]** Extended thinking in Chat, Base, hello repo. `coder-think` is `-c 32768` and
  held the 3090 alone: process 19668 MiB, card 20451/24576 MiB, router on CPU. Thinking on
  (model already warm): question 2.3 s to the first word, 2.5 s for the reply, no run; a
  docstring 1.9 s / 2.5 s, then `needs_you` at 139 s; a plan 13.1 s / 13.9 s, drafted, not
  built. Thinking off: question 4.4 s / 4.8 s and it started a run (`needs_you` at 19 s);
  docstring 4.5 s / 4.7 s, `needs_you` at 13 s; plan 4.5 s / 5.4 s. None came back ready: the
  hello check image is not built, so the checks never ran. The suite with `--think` is still open.

## Published numbers vs ours

Model cards and leaderboards (SWE-bench Verified, LiveCodeBench, Terminal-Bench, Aider) are real
measurements, but of a different setup. Ours differs in ways that move scores a lot:

1. **Thinking.** Cards usually report with reasoning on (often high). Our coder and judge run
   with it off, for speed. That is probably the biggest gap between their numbers and ours.
2. **Quantization.** Cards use full precision. We run Q4/Q5/MXFP4. Usually a few points, but one
   2026 test of a small Qwen on one SWE-bench task saw resolution swing from 0/8 to 8/8 across
   quant levels, in no clean order. Noisy enough that only our own runs settle it.
3. **The harness.** SWE-bench numbers come from agent loops with tools and many turns. Ours is
   one shot with a budgeted prompt, one repair and one fix round. M4b shows the harness can
   matter more than the model.
4. **Context.** Cards test at long context; our coder sees ~10.5k tokens.
5. **The tasks.** Public benchmarks are big real repos and may be in training data. Ours are 36
   small tasks on three small repos, with hidden tests nobody trains on.

So absolute numbers don't transfer. Rankings usually do, but not always (stock Mistral looked
fine on paper as a judge and wasn't). Treat cards as a reason to try a model, and our suite as
the reason to keep it.

## Weaknesses

- **[suspected]** Base with thinking off may be leaving accuracy on the table on the hardest
  tasks. Chat latency is measured (above). The suite with `--think` is not.
- **[measured]** Best is slow: 12,917 s for one suite run, minutes per task.
- **[suspected]** Q4_K_S for the coder was chosen to fit VRAM, not by measuring Q4 vs Q5.
- **[gap]** No comparison against a frontier model on our suite.

## Ideas

| Idea | How we'd know |
|---|---|
| Coder with reasoning on, on the suite (`pablo eval --cascade --think --repeat 3`) | pass rate and false ready against the clean baseline at `2e20e05` (89% ± 7%, false ready 8). Chat first-word times are already in |
| Claude once on the 36 tasks (only suite tasks leave the box; built, on hold until there's an API key) | pass rate and false ready next to ours |
| Q5 coder if it fits with the KV cache | suite, 3 runs, plus tok/s |
| Re-check the leaderboards for newer open coders every month or so | a bake-off when one fits the 3090 |

## Open questions

- How much of the remaining gap to Best is reasoning, and how much is size?
