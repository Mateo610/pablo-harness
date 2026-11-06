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

## Weaknesses

- **[measured]** False ready is the number that matters most to you (it's an approved-looking
  change that's wrong), and it's still 1–3 per run.
- **[suspected]** The judge sees the coder's own tests passing and believes them: it shares the
  coder's blind spot (see verification.md).
- **[gap]** Only one clean run with the Qwen judge. Tonight's run is its real baseline.
- **[suspected]** One threshold for every task type. A refactor and a new feature probably
  deserve different bars.

## Ideas

| Idea | How we'd know |
|---|---|
| Judge with reasoning on, only when certainty lands near the threshold (0.6–0.85) | false ready on replay and suite, plus seconds per judgement |
| Tell the judge which tests the diff added, and ask whether they test the task or the diff | false ready on replay |
| Per task type thresholds, fitted on replay | false ready vs false alarms on replay, then suite |

## Open questions

- Should a plan step get a stricter bar than a one-off change, since later steps build on it?
