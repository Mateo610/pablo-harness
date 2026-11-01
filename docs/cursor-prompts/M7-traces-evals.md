# M7 · Evals you can trust (then traces you can browse)

**Context.** M4b and M4c showed that six hand-scored tasks can't tell two models apart, and that
context (which files reach the prompt) moves results more than the model does. Before M6 changes
context again, we need a fixed suite with an automatic pass condition, so every change after this
has a number to beat. The core track (Claude, 2026-11-01) built it:

- `evals/suite/repos/`: three small seed repos (ledger, textkit, inventory), no third-party deps.
- `evals/suite/tasks.yaml`: 30 tasks, 10 per repo. Each has `refs` (file / symbol / none: how the
  task points at the code), a hidden test the model never sees, and a reference solution used only
  by the self-test. `tests/test_suite.py` proves every hidden test fails on the seed and passes on
  the reference fix, and that a cheating coder (sitecustomize + a skipping conftest) scores 0.
- `pablo eval [--lane coder] [--only ids] [--write]`: each task runs as a normal run (context,
  edits, sandboxed checks, judge) in a throwaway home, so eval traces never become training data.
  Then the diff goes onto a fresh copy with the hidden test, run as
  `python -I -m pytest --noconftest -o addopts=` inside the sandbox.
- Reported: hidden-test pass rate (overall and by refs), no-diff count, how many runs were marked
  ready and how many of those really passed, **false ready** (ready but the hidden test fails:
  the number that must stay near zero), missed (passed but landed in Needs you), mean certainty
  for passing vs failing runs, wall time. `--write` appends to `evals/results.jsonl` and
  `docs/benchmarks.md`.

**Steps (Pablo)**
1. `git pull`, `uv sync`. `uv run pytest -q` must pass (the suite self-test runs locally).
2. `uv run pablo eval --only ledger-by-payee-sums` as a smoke run: the check and hidden test run in
   the container (base image, since the seed repos have no dependency files), the judge swaps in.
3. Baseline: `uv run pablo eval --write` for the coder. All 30 back to back, coder ↔ judge swaps
   included (expect roughly 15–25 min). Do not change any prompt before or during the run.
4. Run it a second time without `--write` and note how many tasks flipped. That is the noise floor:
   a later change has to beat the baseline by more than that to count.
5. Deep, overnight only: queue nothing new; instead, after tonight's queue, run
   `uv run pablo eval --lane deep --write` on a night with nothing queued. That is deep's real
   rematch (30 tasks, hidden tests) and replaces the hand-scored bake-offs.
6. Record the baseline and the noise floor in `docs/decisions.md`. BUILD_PLAN M7 row: the suite
   part done.

**Later (M7b, optional, not blocking M6):** self-host Langfuse (MIT core) bound to 127.0.0.1 with
telemetry off as a trace viewer, fed from the LiteLLM callback; `~/.pablo/traces` stays the source
of truth and escalation traces are tagged do-not-train. Promptfoo (MIT) for prompt A/B can come
with it. Neither is needed to score changes; `pablo eval` is.

**Acceptance**
- `evals/results.jsonl` has a coder baseline on the full 30, with the noise floor recorded.
- `docs/benchmarks.md` has the table. False-ready count is in the decision entry.
