# M2 · Port the good parts of ~/ai-coding-team

> **Done in the core track** (see `docs/decisions.md`, M2 section). Only step 6 (archive the old
> folder on Pablo) is left; run it by hand. The rest is kept for reference.

**Context.** `~/ai-coding-team` is my first version (Qwen agent team, ChromaDB, TOON logger,
complexity predictor, evaluator). This repo replaces it. Read AGENTS.md. Do not modify the old folder
until the last step.

**Goal.** Bring over what earned its keep, in the new structure, with tests. Leave the rest.

**Steps**
1. Inventory `~/ai-coding-team`: list each module, what it does, and a keep / adapt / drop verdict in
   `docs/decisions.md`. Expected keeps: `agents/complexity_predictor.py`, `learning/logger.py` (TOON
   daily files), `learning/evaluator.py`, useful prompts from `agents/specialized_agent.py`,
   `tools/code_tools.py` (ToolResult/TOOL_REGISTRY). Expected drops: the old orchestrator and main.py.
2. **Complexity.** Fold the predictor into `pablo/router.py` as a replacement for `estimate_complexity`
   if it is better. Prove it: label 30 example tasks quick/build/deep in `tests/data/complexity.yaml`
   and compare both; keep the winner.
3. **TOON.** Add `pablo export-training --format toon` that writes the same approved, trainable rows
   in TOON daily files. JSONL stays the source of truth. Keep the training rule intact.
4. **Evaluator.** If the old evaluator scores outputs in a reusable way, move it to `pablo/evals.py`
   (M7 builds on it). Otherwise note why not.
5. **Known bug.** The old test generator truncated output and reported 0/0 tests. Don't port it;
   add a test that the new verifier treats "no tests collected" (pytest exit code 5) as a failure.
6. Archive: `tar czf ~/pablo-data/archive/ai-coding-team-$(date +%F).tgz ~/ai-coding-team` then
   `chmod -R a-w ~/ai-coding-team`.

**Acceptance**
- `docs/decisions.md` has the inventory table.
- New tests for complexity, TOON export, and the exit-code-5 rule pass; full suite, ruff, mypy pass.
- `uv run pablo export-training --format toon` works on real traces from M1.
- BUILD_PLAN M2 marked done.
