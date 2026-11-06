# M7e · Harder evals: holdout, build set, chat behavior, and Claude for scale

**Context.** The 36-task suite is small and noisy (2–7 points of spread), and it can't see plan
mode or chat. Four new measurements (all GPU-free to build, all in this repo):

| Command | What it measures | Rough cost |
|---|---|---|
| `pablo eval --set holdout --cascade --repeat 3 --write` | 12 harder, multi-file tasks on a new seed repo (gradebook). Prints and keeps **totals only** | ~1–1.5 h |
| `pablo eval-build --write` | Plan mode end to end: 5 small tools from an empty project, scored by acceptance tests | ~1–2 h (unknown) |
| `pablo eval-chat --write` (and `--spoken`) | Does Hybrid talk, start one change, or plan, when it should (22 labeled messages) | minutes |
| `pablo eval --lane claude --allow-remote --write` | Claude on the 36 tasks, for scale | ~30 min, a few dollars |

**Rules for the holdout.** It exists so we stop fitting ourselves to the main suite. Never open
`evals/holdout/tasks.yaml` to see why something failed, never paste holdout output into a prompt,
never pick an idea because of it. Its job is to say yes or no about ideas chosen elsewhere. If a
hidden test is wrong, fix it, and say so in `docs/decisions.md`.

**Do this after** agent A's clean Qwen cascade (`pablo eval --cascade --repeat 3 --write`), on
the same commit or newer, with nothing else using the GPU.

## Steps

1. `git pull && uv sync`, then `uv run pytest -q tests/test_holdout.py tests/test_build_eval.py
   tests/test_chat_eval.py` (all pass on the CPU).
2. Chat behavior first, it's quick: `uv run pablo eval-chat --write`, then
   `uv run pablo eval-chat --spoken --write`. Paste the two confusion tables.
3. Holdout baseline: `uv run pablo eval --set holdout --cascade --repeat 3 --write`. You'll only
   see totals; that's the point.
4. Build set: `uv run pablo eval-build --write`. If a project ends with an error (not a fail),
   report the error line and keep going.
5. **Only with Matt's go-ahead** (it sends the 36 suite tasks to Anthropic and costs money; none
   of his code goes): check that `ANTHROPIC_API_KEY` is set for the gateway, decide with Matt which
   model `pablo/escalate` points at in `config/litellm.yaml` for this run (Sonnet is the default;
   Opus if he wants the top), then `uv run pablo eval --lane claude --allow-remote --write`. One
   run is enough. Put `config/litellm.yaml` back if you changed it.
6. Record in `docs/decisions.md` (one row each): chat accuracy per label (typed and spoken),
   holdout mean ± spread and false ready, build set accepted / planned / mean steps, and Claude's
   pass rate and false ready next to Hybrid's. Update `docs/review/evals.md`,
   `docs/review/conversation.md` and `docs/review/models.md` with the numbers (tag them
   [measured]), and the scorecard in `docs/review/README.md`.

## Report back

The four headline numbers, the chat confusion tables, the build table from `docs/benchmarks.md`,
and anything that errored. Don't report which holdout tasks passed: the command doesn't show it.
