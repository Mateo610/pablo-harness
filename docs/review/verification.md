# Verification: checks and the sandbox

## What it does

Every diff is applied to a scratch copy and the repo's checks run there (`pytest -q`, mypy if
configured, and an `imports` check that imports each module on its own). On Pablo, checks run in
rootless Podman with gVisor, one container per check, pinned to their own cores, no network. A
check that fails is re-run on the unedited copy, so a verdict blames the diff only for what it
made worse. Optional task-derived checks (`spec.py`, off) have the judge model write up to 5
tests from the request before it sees the diff.

## What we know

- **[measured]** The "worse than before" rule stopped the judge blaming changes for failures
  that were already there (M4c).
- **[observed]** The imports check catches modules that break on import but aren't tested
  (added 2026-11-01; never counted on its own).
- **[measured]** Spec checks (M7d): false ready went from 7 to 2 across three runs, and they
  caught `harness-memory-units` in 3 of 3 runs, the first thing that ever did. But false alarms
  averaged 2 per run against a limit of 1, so they stay off. The comparison runs were also hit by
  deep 500s (see `decisions.md`, 2026-11-06).

## Weaknesses

- **[measured]** The central blind spot: when the coder misreads the task, it also writes a test
  that agrees with its misreading. The checks pass, the diff reads fine, and the hidden test
  fails. `textkit-reading-time` still slipped past spec checks in 2 of 3 runs.
- **[measured]** Spec false alarms were mostly the check guessing the code wrong:
  `harness-router-unknown-lane` built a settings object incorrectly and errored before testing
  anything (3 of 3 runs), `textkit-wrap-max-lines` expected a wrap the function doesn't do,
  `ledger-move-format` guessed a negative-money format.
- **[gap]** New projects start with no tests, so the first steps of a plan are verified only by
  imports and the judge.

- **[observed]** Fixed (2026-11-06, `reliability-fixes`): approving a whole diff applied it with no
  new check, even when the repo had changed since (another approval, your edits). Each verdict now
  records the repo's `tree` hash; approve re-checks when it differs and stops a change that was
  clean and isn't any more (`StaleChecksFailed`, 409) unless you force it. Two approvals of one
  run at once apply it once.

## Ideas

| Idea | How we'd know |
|---|---|
| Spec: count only assertion failures as "failed"; an error before any assertion is "broken" (ignored) | false alarms per run on the suite **and** on the holdout set (designed after seeing suite failures, so the suite alone would flatter it) |
| Spec checks only for tasks with concrete examples in the request (numbers, strings, "should return") | false alarms and catches per run |
| Plan mode: make step 1 of every new project "a test runner and one passing test" | plan-mode eval: fewer late steps that break earlier ones |

## Open questions

- Can a second model write the tests (not the judge model) without making the run much slower?
