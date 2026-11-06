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

## Weaknesses

- **[measured]** False ready is the number that matters most to you (it's an approved-looking
  change that's wrong), and it's still 1–3 per run.
- **[suspected]** The judge sees the coder's own tests passing and believes them: it shares the
  coder's blind spot (see verification.md).
- **[measured]** On the suite the Qwen judge is not better than the heretic, and it may be more
  cautious (7 vs 3 right changes held back) and a little slower. The replay bake-off (same 143
  diffs, both judges) said the opposite. 45 new replay diffs from `2e20e05` can settle it.
- **[suspected]** One threshold for every task type. A refactor and a new feature probably
  deserve different bars.

- **[suspected]** Our setup leans toward the heretic: the Qwen judge runs with reasoning off
  (the heretic has no thinking mode to lose), the judge prompt's 0.2/0.3 anchors were written
  from the heretic's mistakes (Nov 1–2), and the suite scores a reviewer's fair question on a
  change whose hidden test passes as a miss. M7f measures the first on replay.
- **[measured]** Both judges are poorly calibrated on subtle errors: on wrong changes the Qwen
  judge's median certainty was 0.9, the same as on right ones; the heretic's was 0.8 vs 1.0.
- **[observed]** One Qwen reply at `2e20e05` was unscorable (no JSON), and the judge's reply budget
  is 300 tokens. M7f looks at why.

## Ideas

| Idea | How we'd know |
|---|---|
| Judge with reasoning on, only when certainty lands near the threshold (0.6–0.85) | false ready on replay and suite, plus seconds per judgement |
| Tell the judge which tests the diff added, and ask whether they test the task or the diff | false ready on replay |
| Per task type thresholds, fitted on replay | false ready vs false alarms on replay, then suite |

## Open questions

- Should a plan step get a stricter bar than a one-off change, since later steps build on it?
