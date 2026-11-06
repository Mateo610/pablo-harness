# U1 · How good is an unfiltered project? The advisor model as coder and judge

**Context.** A no-guardrails project runs everything on Mistral Small 3.2 24B heretic: it writes
the code and judges it, with no Best retry and no Claude (`src/pablo/projects.py`,
`unfiltered_settings`). Nobody has measured that model as a coder. `pablo eval --unfiltered`
scores exactly that setup on the 36-task suite.

## Steps

1. `git pull && uv sync`. Nothing else on the GPU.
2. `uv run pablo eval --unfiltered --repeat 3 --write`. The first lines should say
   `preflight: pablo/advisor · pablo/advisor · pablo/advisor` (one model for everything).
   It's reported as lane `unfiltered`.
3. Record a row in `docs/decisions.md`: passed per run, mean ± spread, false ready, no-diff
   count, wall, next to Base alone (`5fe0997`: 30, 32, 31) and Hybrid (`2e20e05`: 34, 29, 33).
   Note whether it judged its own broken work as ready more often than the Qwen judge does.
4. Update `docs/review/projects.md` (replace the "[gap] never been measured as a coder" line with
   the numbers, tagged [measured]).

## Report back

The three pass counts, mean ± spread, false ready, and anything odd in how it writes edits
(no-diff answers, blocks that don't apply).
