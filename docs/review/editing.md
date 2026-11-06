# Editing: how changes are written

## What it does

The coder answers with find-and-replace blocks (`edits.py`); git turns them into a diff. A block
that doesn't match gets one repair pass with the exact error and the closest lines. If the change
makes a check worse, the coder sees the failing output once and may fix it (fix-on-red,
`fix_rounds: 1`). Unified diffs are still accepted.

## What we know

- **[measured]** The block format fixed malformed diffs: the M4 soak reran three sandbox tasks 20
  times; 0 needed a repair pass, 20/20 were ready.
- **[measured]** Fix-on-red scored 25/30 vs a 23/30 baseline, equal to the noise floor. Kept
  because it costs little, but never proven.

## Weaknesses

- **[measured]** We don't know what fix rounds actually change: how often they turn a failing run
  into a passing one vs hide a failure (the check passes, the hidden test still fails).
- **[observed]** "No diff" answers still happen (5–7 per run in the damaged cascade runs, mostly
  deep 500s; 0 in the clean one). With 500s guarded now, we should see whether any remain.
- **[suspected]** One-shot editing of a new project's first file (empty repo) has no examples of
  the style it should follow.

## Ideas

| Idea | How we'd know |
|---|---|
| Report, per eval, fix rounds tried / kept / kept-and-hidden-passed (the fields exist; read them together) | if kept-and-passed ≈ kept, fix-on-red earns its place; if not, it hides failures |
| A second fix round only when the first got closer (fewer failing tests) | suite, 3 runs |

## Open questions

- Should a plan step that fails its checks retry automatically before it pauses the plan?
