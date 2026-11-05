# M7d · Measure task-derived checks, then decide

**Context.** No judge in the bake-off caught `harness-memory-units` or `textkit-reading-time`. The
coder writes a test that agrees with its own wrong answer, the checks pass, and the diff reads fine.
Task-derived checks (`src/pablo/spec.py`, off by default) work around that blind spot:

- Before seeing the diff, the judge model writes up to 5 pytest tests from the concrete facts in the
  request.
- Pablo runs them in the sandbox, with `--noconftest`, on the unedited code and on the changed code.
- **failed** (an assertion fails on the change): caps certainty at 0.5 and asks you, quoting the
  assertion.
- **confirmed** (fails before, passes after): extra evidence for the judge.
- **broken** (the check guessed the code wrong) or **uninformative**: ignored.

It only runs when the repo's own checks are clean, so it costs one extra judge-model call (no swap)
and two short pytest runs per clean run.

**Do this after the judge switch to Qwen3.8-27B dense** (agent A), so both arms use the same
judge. Don't run it while the voice agent is measuring latency.

**Steps**
1. `git pull`, `uv sync`. Leave `spec.enabled: false` in `config/lanes.yaml`.
2. Without spec, on the same commit and judge: `uv run pablo eval --cascade --repeat 3 --write`.
   If agent A's post-switch cascade run is on this exact commit, reuse it instead.
3. With spec: `uv run pablo eval --cascade --spec --repeat 3 --write`.
4. Report, per arm: mean pass rate ± spread, and false ready per run (which tasks).
   From the spec arm also report:
   - the `spec` status counts
   - `spec_caught`: spec failed and the hidden test failed (a real catch)
   - `spec_false_alarm`: spec failed but the hidden test passed (it blocked good work)
   - the added wall time per run
   For every false alarm, paste the generated test (it's in the run's `spec.source`) and say what
   it got wrong.
5. Decision rule:
   - Turn it on (`spec.enabled: true`) if false ready drops, false alarms average ≤ 1 per run, and
     the pass rate doesn't drop. Otherwise leave it off and write up why.
   - Either way, add a `docs/decisions.md` row with the numbers.
   - Don't edit `SPEC_PROMPT` to fix a specific suite task. That would be tuning on the test.
6. Run `uv run ruff format src tests` and confirm CI passes.

**Don't**
- Don't put generated spec tests in training data or in the suite. They are evidence for one run.
- Don't let spec failures fail a run. They only send it to Needs you, and that is by design.
