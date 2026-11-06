# Context: what the coder gets to see

## What it does

`context.py` builds the coder's prompt under a token budget (`context_tokens`, 10.5k for the
coder, 24k for Best): the task, one skill card, a repo tree, then whole files picked by the
symbols the task names (named paths, modules, files defining a named symbol, files using it,
then BM25 on the repo map). `repomap.py` keeps a tree-sitter symbol index per repo. If the coder
edits a file it wasn't shown, the repair turn shows only the files its edits touch.

## What we know

- **[measured]** Context is where the early wins came from. Before the M4c fixes (skip `.venv`,
  follow symbols), gpt-oss was no better than the coder on six hard tasks; after them it scored
  34/36 on the suite. Different tests, so not a clean before/after, but the M4b write-up blamed
  context, not the model.
- **[measured]** The repo map alone was not a keep (79% ± 2%, false ready 5, vs 82% ± 6%).
  Two-step lookup plus map: 84% ± 2%, false ready 7. Better mean, inside the spread, more false
  ready. Both stay in the code, off or neutral.
- **[measured]** A missed-file repair used to overflow the 16k window with a 400 error; fixed by
  `repair_context` (2026-11-04).

## Weaknesses

- **[observed]** 10.5k tokens is about four medium files. On a real project the right file is
  often the fifth. The prompt has no way to ask for more except the two-step lookup, which didn't
  show a gain.
- **[suspected]** We never measured *why* failures fail: was the right file in context or not?
  Without that, context changes are guesses.
- **[suspected]** Picking whole files wastes budget on long files where one function matters.
- **[gap]** Plan mode tells each step the goal and what's done, but not the actual code from
  earlier steps beyond what file picking finds.

## Ideas

| Idea | How we'd know |
|---|---|
| Log, per eval task, whether every file the reference solution touches was in context | a "context recall" number next to pass rate; failures split into "not shown" and "shown but wrong" |
| Function-level slices for long files (definition plus callers) instead of whole files | context recall up at the same budget, then pass rate |
| For plan steps, include the diff of the previous approved step | plan-mode eval (see evals.md) |
| Coder with a bigger window (32k) at some speed cost | suite, 3 runs, plus tok/s |

## Open questions

- Is the coder's limit the window size, or how well it uses what it already sees?
