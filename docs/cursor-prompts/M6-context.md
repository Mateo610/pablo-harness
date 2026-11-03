# M6 · Better context: repo map, then symbol lookup

**Context.** `context.pick_files()` (M4c) follows the symbols a task names: named paths, modules
(`report` in `report.monthly`), files that define or use a named symbol, then the rest ranked by
task words. On the three small seed repos that is mostly enough. Where it fails, it fails on words:
`inventory-remove-too-many` says "quantity", the code says `qty`, and the prompt held only
`__init__.py`. The real test is a repo the size of this harness, so the suite now has 6 tasks on a
pinned commit of it (`seeds.harness` in `evals/suite/tasks.yaml`), 36 tasks in all.

**The number to beat.** Before touching context, run the baseline on the 36-task suite:

```
uv run pablo sandbox build --suite      # once: builds the harness seed's dependency image
uv run pablo eval --repeat 3 --write    # overnight: ~1.5 h, three full runs, mean ± spread
```

M6 is a keep only if its 3-run mean beats that mean by more than the spread, with false ready not
going up. Harness tasks and `refs: none` tasks are where it should show. Note the per-task `context`
file lists in the results: a pass that came from the right file reaching the prompt is the claim.

**Steps**
1. Repo map: a symbol map (file → classes/functions with signatures and the first docstring line)
   with tree-sitter. Check the license of whichever binding you use (tree-sitter is MIT; grammar
   packs vary) and record it in THIRD_PARTY.md. Rank symbols for a task with BM25 over symbol
   names, split identifiers (`qty` → also matches "quantity" via a small synonym/abbreviation list
   is fine, keep it in one place), docstrings, and paths. Put the map (not whole files) into the
   prompt's `tree` section, and use its ranking to order `pick_files` after the named/defined
   files. The budgets in `context.Budget` still apply. Keep the `context` event's file list.
2. Cache the index in `~/.pablo/index/<repo hash>`; rebuild when git HEAD or the tree hash changes.
3. Measure: `pablo eval --repeat 3 --write` with the map. Stop here if it already wins.
4. Only if the map alone doesn't win: Serena (MIT, LSP over MCP) as a separate process, with a
   two-step coder flow (first call lists the symbols it needs, the harness fetches definitions and
   references, the second call writes edit blocks). One-shot stays the fallback. Measure again.
5. Tests run without tree-sitter grammars or Serena installed (mock both); CI stays green.

**Acceptance**
- The 36-task baseline (3 runs) and the M6 result (3 runs) are both in `docs/benchmarks.md` and
  `evals/results.jsonl`, with the decision in `docs/decisions.md`.
- No regression on the 30 seed-repo tasks beyond the spread; false ready no higher than baseline.
- BUILD_PLAN M6 marked done only if it's a keep; otherwise record what didn't work.
