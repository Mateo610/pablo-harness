# M4c · Rerun the deep bake-off with real context

**Context.** The M4b bake-off (`docs/benchmarks.md`) scored gpt-oss-120b and Qwen3.5-122B-A10B at
1 better, 1 worse, 4 same against the coder. Claude's read of the tables: the result tracked which
files reached the prompt, not the models. Until now the prompt held only files whose path appears
in the task, else the first six sources. Every task that named its file got a real attempt; every
task that named only functions (`load_line`, `report.monthly`) got no diff or an invented answer.
Deep also got the coder's 10.5k-token prompt despite a 64k window.

**What changed in the core track (pull first).**
- `context.py` picks files from the symbols a task names: modules (`report` in
  `report.monthly`), files that define a named function or class, files that use it (usually the
  tests), then the rest ranked by task words. A file that quotes the task (an eval list) is skipped.
- Each lane has `context_tokens`. Deep is 24000 (65536 window − 32768 reply, with room for code
  tokenizing worse than 4 chars per token). A big window also takes the rest of a small repo.
- Every run emits a `context` event listing the files its prompt carried (`pablo replay <run>`).
- Checks compare with the unedited copy when they fail. `status` is passed, regressed, already
  failing (no more failures than before), still failing, or failed. The judge's 0.3 cap and
  `ready` use `verdict.clean` (nothing worse than before), not "all green".
- The judge prompt gained two lines: one for "already failing" checks, one naming the trick
  gpt-oss used on bug-load-line (a new module that monkeypatches the function under test).
  `evals/judge/cases.yaml` has `bad-patched-not-fixed`, built from that run. Main is now 16 cases.

**Steps**
1. `git pull`, `uv sync`, restart `pablo serve` so the scheduler loads the new code.
2. Judge, because its prompt changed: `uv run pablo eval-judge --set main --write` must be 16/16.
   If `bad-patched-not-fixed` scores 0.75 or more, tune the prompt on main only, then rerun main.
   When main is 16/16, run `--set holdout --write` once and record it, with no tuning after.
   Also record what the judge gave the original run `f1138054e4`.
3. Why the harness repo's own checks are red at baseline. First guess: `python:3.11-slim` has no
   `git`, and the harness tests run `git init`. Add `git` to `scripts/sandbox/Dockerfile` with
   apt (`--no-install-recommends`, then clear the apt lists), rebuild the base, rebuild the harness
   project image, `podman save` the new base, and update the digest note. A scratch-copy check of
   the harness repo must pass `pytest -q` and `mypy` inside runsc. If the cause is something else,
   fix that and record it.
4. Sort every no-diff run from both bake-offs (the records in `~/.pablo/runs`): the edits didn't
   apply (SEARCH missed), the model replied without edit blocks, the reply hit `max_tokens`
   (completion tokens at the cap), or a timeout. One table in `docs/benchmarks.md`.
5. Rerun the six tasks in `evals/deep/tasks.yaml` with `pablo compare --lanes coder,deep` on
   gpt-oss-120b (it's still on the swapper). For each pair, also note the `context` event's file
   list for both lanes. Score by hand as before (better / same / worse) with the same keep rule:
   better on 4+ = keep; worse or tie on 4+ = deep stays off; anything else = no clear win.
   Do not rerun Qwen unless gpt-oss loses again and the context lists show the right files.
6. Record the decision in `docs/decisions.md` and the M4c row in `docs/BUILD_PLAN.md`.

**Acceptance**
- `eval-judge --set main` 16/16 with the new case; holdout recorded once.
- The harness repo's checks are green in the sandbox at baseline.
- The no-diff table and the rerun table are in `docs/benchmarks.md`, each pair with its file lists.
- The keep or drop decision for deep is recorded with the rerun's score.
