# Judge: certainty and "ready"

## What it does

After the checks, the judge (Qwen3.8-27B dense, reasoning off) reads the task, the diff and the
check results and returns a certainty. At or above 0.75 with clean checks, the run is **ready**;
below, it **needs you** with a question. The prompt names known cheats (monkeypatching, deleted
tests, ignoring a stated constraint). Swapping from the coder costs ~7.6 s; a judgement ~2.3 s.

## What we know

- **[measured]** The bake-off on 143 replayed suite diffs (18 broken) picked Qwen dense when
  false ready counts 3× a false alarm. Stock Mistral would have flagged 55 of 125 good runs.
- **[measured]** False ready (judge says ready, hidden test fails) per 3-run trio: 4–7 for the
  coder alone, 5–9 for the cascade with the old judge, and 4 in the one clean Qwen run (one run,
  not three).
- **[measured]** No judge has caught `harness-memory-units` or `textkit-reading-time`.
- **[measured]** Clean Qwen cascade (`2e20e05`): 34, 29, 33 of 36 (96/108), false ready 8, judge
  said "needs you" on 7 changes that were right, mean wall 2943 s. Heretic cascade (`5fe0997`,
  `84a7349`): 33, 32, 34 (99/108), false ready 5, 3 right changes held back, 2586 s. Task by task,
  8 tasks differ by one run each, 5 favoring the heretic and 3 the Qwen judge: noise. Leaving out
  the two tasks no judge catches, false ready is 2 vs 3.
- **[measured]** Same 275 replay diffs (33 broken), threshold 0.75, weight 3× false ready + a
  missed review (`085f0a7`). Qwen reasoning off: false ready 12, missed 28, weighted 64, 2.21 s.
  Heretic: 15, 23, 68, 1.20 s. Qwen reasoning on (4096 tokens): 7, 57, 78, 33.9 s. Clean-but-broken
  caught 16/28, 13/28, 21/28. Reasoning-off Qwen beats the heretic on this set. Reasoning on is
  stricter and about 15× slower; at 0.75 the extra reviews lose the weighted score. Its best
  cutoff on the sweep is 0.5 (false ready 8, missed 30, weighted 54). Qwen-off does not move
  between 0.5 and 0.9.

## Weaknesses

- **[measured]** False ready is the number that matters most to you (it's an approved-looking
  change that's wrong), and it's still 1–3 per run.
- **[suspected]** The judge sees the coder's own tests passing and believes them: it shares the
  coder's blind spot (see verification.md).
- **[measured]** On the suite the Qwen judge is not better than the heretic (96/108 vs 99/108,
  false ready 8 vs 5), inside the noise. On the 275-diff replay it is better (weighted 64 vs 68,
  false ready 12 vs 15). The suite and the replay still disagree.
- **[suspected]** One threshold for every task type. A refactor and a new feature probably
  deserve different bars.

- **[measured]** Reasoning off is not what makes Qwen lose to the heretic on replay: with it off,
  Qwen's weighted score is better (64 vs 68). Turning reasoning on cuts false ready (12 → 7) and
  catches more clean-but-broken (16/28 → 21/28), and costs 33.9 s instead of 2.21 s. The
  heretic-tuned 0.2/0.3 anchors were left as they are; a prompt change would be tested on
  `evals/judge` and the holdout, not on these diffs.
- **[measured]** Both judges are poorly calibrated on subtle errors: on wrong changes the Qwen
  judge's median certainty was 0.9, the same as on right ones; the heretic's was 0.8 vs 1.0.
- **[measured]** The unscorable `textkit-truncate-words` reply (run `c6bcbbab1a`, certainty 0) was
  cut off at 300 tokens. Replayed, `finish_reason` was `length` and the text opened
  `{"certainty": 0.3, "summary": ...` then spent the budget deliberating inside the summary, so
  the object never closed and did not parse. Not prose before the JSON.
  Fixed: the reply budget is 600 tokens, and a reply cut off after its score keeps the score,
  capped at 0.7 so it always asks you.

## Ideas

| Idea | How we'd know |
|---|---|
| Judge with reasoning on, only when certainty lands near the threshold (0.6–0.85) | Always-on reasoning is measured (false ready 7, 33.9 s, weighted 78 at 0.75). The near-threshold version is not |
| Tell the judge which tests the diff added, and ask whether they test the task or the diff | false ready on replay |
| Per task type thresholds, fitted on replay | false ready vs false alarms on replay, then suite |

## Open questions

- Should a plan step get a stricter bar than a one-off change, since later steps build on it?
