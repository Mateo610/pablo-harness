# M7f · Judge recheck: is the setup holding the Qwen judge back?

**Context.** On the clean cascade (`2e20e05`) the Qwen judge was no better than the heretic it
replaced: 96 vs 99 of 108 passed, false ready 8 vs 5, and 7 vs 3 right changes held back. All
inside the noise, but it contradicts the replay bake-off that picked Qwen. Three things in our
setup lean toward the heretic, and this prompt measures each one on the **replay** set (the same
diffs for every judge), which is minutes of GPU, not hours:

1. **Reasoning is off** for the Qwen judge (`--reasoning off`), for speed. Qwen3.8-27B is a
   thinking model; the heretic has no thinking mode, so it runs at full strength.
2. **The judge prompt was tuned with the heretic as judge** (Nov 1–2: the 0.2 / 0.3 anchors came
   from its mistakes on `evals/judge` fixtures).
3. **The suite counts a stricter reviewer as wrong.** Several of the 7 held-back changes had real
   reviewer questions (unrelated files changed, `qty >= 10` vs `> 10`), but their hidden tests
   passed, so they count as misses.

One judge reply in that run also came back unscorable ("Judge could not score this") on
`textkit-truncate-words`. Find out why.

## Steps

1. `git pull && uv sync`. Reload llama-swap and the gateway so the new `judge-qwen-think` entry
   (same weights, `--reasoning on`) and the `pablo/judge-qwen-think` alias exist.
2. Replay all three on the same diffs (`evals/judge/replay.jsonl`, now with 45 more from
   `2e20e05`). Each writes its table to `docs/benchmarks.md`:
   ```
   uv run pablo eval-judge --set replay --model pablo/judge --write
   uv run pablo eval-judge --set replay --model pablo/advisor --write
   uv run pablo eval-judge --set replay --model pablo/judge-qwen-think --max-tokens 4096 --write
   ```
   Time each one (seconds per judgement matters: the judge runs on every change).
3. The unscorable reply: find the `textkit-truncate-words` run at `2e20e05` that has certainty 0
   (evals/results.jsonl has its `run_id`), and look at the judge's raw reply in that run's trace
   in the eval's home if it still exists, or replay that one case with `--model pablo/judge` and
   print the raw text. Was it cut off at 300 tokens, prose before the JSON, or something else?
4. **Don't edit the judge prompt to fix replay misses.** The replay labels come from the suite's
   hidden tests, so tuning the prompt on them is tuning on the suite (rule 7). If the prompt looks
   unfair to Qwen, write down why, and we'll test a change on `evals/judge` (the hand-made
   fixtures) and the holdout instead.
5. Record one row in `docs/decisions.md`: the three replay results side by side (false ready,
   false alarms, the weighted score, seconds per judgement), and what the unscorable reply was.
   Update `docs/review/judge.md`.

## Report back

The three replay tables, the timing, and the cause of the unscorable reply. Then Matt decides:
keep Qwen as is, Qwen with reasoning (if it wins and isn't too slow), or back to the heretic.
