# M7c · Fresh baselines, the offline drill, and the judge bake-off

**Context.** The 3-run rematch on 471ec8f put the coder at 88% ± 2% and the cascade (coder → deep)
at 88% ± 4% for 2.7× the wall time. Three things came out of it:

- `harness-clip-small-caps` failed in all 5 runs with `400 Bad Request`. The missed-file repair
  stacked context.py on the full first prompt and overflowed the coder's 16k window.
- Most cascade retries re-solved clean coder runs that were already right; the judge was only
  unsure. The one real win (`inventory-move-bulk`) had failing checks.
- The failures that cost you are false ready: `textkit-reading-time` and `harness-memory-units`
  were called ready at 0.9 and broken in all 5 runs. Retries never see those. Only a better judge
  catches them.

**What changed in the core track (pull first).**
- The repair turn shows only the task and the files the edits touch when the coder edited a file
  it was never shown. A run that fails outright (gateway or model error) retries on `retry_on`.
- New lane setting `retry_if: broken` (default). Deep only retries runs that failed, made no diff,
  didn't apply, or whose checks got worse. `needs_you` brings back the old behavior.
- `pablo eval --write` now also keeps every judged diff in `evals/judge/replay.jsonl`: task, diff,
  the checks the judge saw, and whether the hidden test passed (as a label only; the hidden test
  and its output are never stored). Duplicates (same task, same diff) are kept once.
- `pablo eval-judge --set replay --model <alias> [--write]` replays those diffs to any judge and
  reports false ready, broken-but-clean caught, missed, and a threshold sweep from the same
  certainties (no extra calls).
- New lane `advisor` (`pablo/advisor`): the heretic Mistral, `writes_code: false`,
  `routable: false`, `train_ok: false`. `config/llama-swap.yaml` and `config/litellm.yaml` have
  an `advisor` entry with the same weights as `judge`. Until the bake-off is done, both point at the
  heretic.

**Steps**
1. `git pull`, `uv sync`. Copy `config/llama-swap.yaml` and `config/litellm.yaml` to where the
   services read them if they aren't symlinked. Restart `pablo-swap`, `pablo-gateway`, `pablo`.
   Smoke: `uv run pablo run --lane advisor "one sentence: what are you?"` answers, and
   `pablo doctor` is green.
2. Offline drill (M10 step 2): `bash scripts/offline-drill.sh`. Its suite pass uses `--write`, so it
   also counts as coder run 1 of 3 below and seeds the replay. Record the drill result in
   `docs/decisions.md`, then `--undo` if the script didn't.
3. Two more coder runs: `uv run pablo eval --repeat 2 --write`. Report all three together (mean ±
   spread, false ready) and whether `harness-clip-small-caps` stopped failing with a 400.
4. Cascade with the new rule: `uv run pablo eval --cascade --repeat 3 --write`. Report the pass
   rate, how many tasks retried (it should be far fewer than 14), and the wall time against
   ~2930 s a run.
5. Judge baseline on the replay: `uv run pablo eval-judge --set replay --write` (the heretic, as
   `pablo/judge`). After steps 2–4 the replay should hold roughly 100–200 unique diffs, about 10%
   of them broken. If it holds fewer than 15 broken diffs, say so before the bake-off; the result
   would be noise.
6. Bake-off candidates. Before downloading each one, check that the GGUF exists on Hugging Face
   and that the license is Apache-2.0 or MIT, then add a `THIRD_PARTY.md` row:
   - `judge-mistral`: stock Mistral-Small-3.2-24B-Instruct-2506, Q5_K_M (same size as now, guardrails on).
   - `judge-oss20`: gpt-oss-20b MXFP4 (small, fast, reasoning; `--reasoning-effort low`).
   - `judge-qwen-dense`: the newest dense Qwen around 27–32B that fits at Q4/Q5 with 16k context.
     Name the exact repo you pick.
   - `judge-coder`: no download. Point a judge alias at the coder's llama-swap entry. It skips the
     3.7 s + 5.1 s swap on every run, but it judges its own work, which the replay will measure.
   Add each as a llama-swap `gpu` member and a `pablo/<name>` LiteLLM alias. Don't change the
   `judge` lane yet.
7. For each candidate and the heretic: `--set replay`, `--set main`, `--set holdout`, all with
   `--write`, plus seconds per judgement and VRAM. Never tune a prompt on replay or holdout.
8. Pick. Lowest false ready on replay at the model's best threshold from the sweep wins. Ties go
   to fewer missed, then holdout, then speed including swap. Stop and report the table before
   changing `lanes.yaml`. Matt picks.
9. After Matt picks: point `pablo/judge` at the winner, set `thresholds.judge_needs_you` to its
   threshold, archive the weights (`scripts/archive-model.sh`), and run `pablo eval --repeat 3
   --write` once more so the suite's false ready reflects the new judge.

**Don't**
- Don't put replay cases, suite tasks, or hidden tests in any training export. `prepare_training`
  reads traces only. Keep it that way.
- Don't send anything to Claude. Every judge here is local.
- Run `uv run ruff format src tests` before every commit and wait for CI to pass.
