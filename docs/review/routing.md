# Routing and Hybrid

## What it does

The router (Qwen3.5-4B on the CPU, plus keyword rules and a complexity estimator) picks a lane
for each task. Deep is never picked on its own (`routable: false`); Claude only by escalation.
**Hybrid** (the chat menu's default) is the cascade: Base builds, checks and the judge review,
and a broken change (failed, no diff, didn't apply, or checks worse) is retried on Best right
away. Picking a model by name means that model alone, no retry.

## What we know

- **[measured]** The complexity estimator scores 27/30 on `tests/data/complexity.yaml`; the
  legacy 1–10 predictor scored 19/30.
- **[measured]** Hybrid with `retry_if: broken`: 92% ± 3%, retries 10, of which 9 passed
  (`5fe0997`). Retrying every Needs-you run spent most retries re-solving correct changes.
- **[measured]** The cascade is +6 points over the coder alone; most of it is deep fixing the
  coder's broken runs.

## Weaknesses

- **[observed]** Hybrid's gain comes from Best, which takes minutes and evicts the coder. On a
  busy evening a Hybrid retry blocks everyone else's chat for its length.
- **[suspected]** "Broken" retries miss the dangerous case: clean checks, wrong answer, judge
  fooled. Those land as ready and never retry.
- **[gap]** Learned routing (send a kind of task straight to Best when it clearly does better in
  this repo) is built and off; it needs real outcomes.
- **[gap]** The router has never been measured on chat messages, only on task text.

- **[observed]** Fixed (2026-11-06, `reliability-fixes`): a retry queued for tonight still ran
  after you approved the run it retried, and could hand you a second diff for a done task.
  Approving now cancels it, and the night runner skips any job whose group has an approved run.
  Night retries keep who started them (and their notes). The queue file takes an flock for every
  change, so a retry thread, `pablo serve` and the CLI can't drop each other's jobs.

## Ideas

| Idea | How we'd know |
|---|---|
| Turn on learned routing after a few weeks of real approvals | outcomes per kind before vs after; time to ready |
| Hybrid retry on Best only when the judge is also unsure (certainty < 0.6), not only on broken | suite: false ready and wall time |
| Show the expected wait for Best in the menu ("Best: ~3 min right now") from the GPU queue | fewer abandoned Best requests (observed) |

## Open questions

- Is it better for Hybrid to retry on the base model with reasoning on, before going to Best?
