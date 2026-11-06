# Evals

## What it does

`pablo eval` runs 36 tasks on four seed repos (ledger, inventory, textkit, and a pinned copy of
this harness). Each task is scored by a hidden test the model never sees, run with
`python -I -m pytest --noconftest` after restoring the original tests. Runs happen in a throwaway
home, so nothing from the suite reaches traces or training. `--repeat 3` reports mean, spread,
flaky and never-passed tasks. `eval-judge` scores judges on replayed real diffs.

## What we know

- **[measured]** The spread across 3 runs of the same code is 2–7 points (e.g. 79% ± 6%,
  82% ± 6%, 84% ± 2%). An idea has to move the mean by more than that to count.
- **[measured]** Two tasks fail almost every run with every setup: `harness-memory-units`,
  `textkit-reading-time`. A few more come and go (`textkit-slug-max-length`,
  `harness-clip-small-caps`, `inventory-remove-too-many`).
- **[measured]** Runs 2–3 of the first Qwen cascade, and all three spec runs, were damaged by deep
  500s (the voice server held VRAM). Since `05b9836`, an eval checks every model first and stops
  instead of scoring a crash as a wrong answer.

## Weaknesses

- **[observed]** The tasks are small and well specified: one or two files, a clear behavior. Real
  work (Plant Watcher from scratch, a feature across five files) isn't represented. The suite
  flatters local models and can't see what plan mode or projects change.
- **[measured]** Too noisy for small effects. With a 2–7 point spread, most single ideas
  (fix-on-red, repo map, two-step) ended up "not a keep" without us knowing whether they helped.
- **[gap]** No held-out set. We look at failures of these same 36 tasks to decide what to try, so
  over time we risk tuning to them (rule 7 forbids prompts tuned on hidden tests, but choosing
  ideas from suite failures is a softer version of the same thing).
- **[measured]** Chat behavior at `2563ea6`: Hybrid typed 14/22, spoken 16/22 (tables in
  conversation.md). First spoken word, and whether "apply it" lands, are still unmeasured.
- **[observed]** A run takes 25–45 minutes (hours for Best alone), so one evening buys one
  comparison.

## M7e · 2026-11-06

- **[gap]** `pablo eval --set holdout`: 12 harder tasks (gradebook repo), totals only.
- **[gap]** `pablo eval-build`: plan mode on 5 small tools, acceptance tests on the result.
- **[measured]** `pablo eval-chat` at `2563ea6`: typed 14/22 (talk 3/8, run 5/8, plan 6/6),
  spoken 16/22 (talk 2/8, run 8/8, plan 6/6).
- **[gap]** `pablo eval --lane claude --allow-remote`: Claude on the main suite, for scale. On hold
  until there is an API key.

## Ideas

| Idea | How we'd know |
|---|---|
| More runs per comparison overnight (5 instead of 3), using the overnight window | spread shrinks enough to see 3-point effects |
| Paired scoring: report per-task flips between A and B, not just means (a sign test) | fewer "can't tell" results at the same run count |
| Grow the holdout to ~25 tasks on a second new seed repo, so a single run means more | spread on the holdout |

## Open questions

- How big must the suite be for a 3-point change to show in one night?
