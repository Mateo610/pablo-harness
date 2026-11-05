# M8b · Learning without retraining: practice, examples, failure notes, routing

**Context.** The M8 LoRA trains with a 256-token window (`PABLO_TRAIN_MAX_SEQ`), and a real coding
example is 5–10k tokens, so today it only learns from fragments. Phase 1 is learning that doesn't
need training. It's built in the core track and off until measured:

- **Practice** (`pablo practice`): takes a function your repo's tests use, removes its body or
  slips in one small bug, has the coder fix it in a throwaway copy, and keeps only fixes that pass
  the original tests. Tests are restored before scoring; a fix that edits tests, conftest, or
  sitecustomize is thrown out. Deep tries what the coder missed (`teacher`). Anything the eval
  suite names is skipped in every repo, and suite folders are refused.
- **Lessons** (`~/.pablo/lessons/`), recorded always:
  - examples: your whole approvals plus kept practice fixes
  - failure notes: the first error of a check that got worse
  - outcomes: which lane got which kind of task right
- **Using them**, each off: `learn.examples` (1–2 right answers in the coder prompt; the file
  context shrinks to make room), `learn.failures`, and `learn.routing` (deep for a task type when
  it clearly beats the coder, with at least 5 tries each).
- Practice runs at night urgency through the GPU scheduler, so it waits for quiet and never
  pushes anyone off the GPU.

**Run this after M7d** (the spec-check evals) and outside the voice agent's latency measurements.

**Steps**
1. `git pull`, `uv sync`.
2. Smoke on a real repo you own (not the eval suite): `uv run pablo practice --repo <repo> --n 5`.
   Then `uv run pablo lessons`. Paste the kept/missed lines. For two kept examples, read the diff
   and say whether it's a fix you'd approve.
3. Suite-seed lessons, for measuring:
   `uv run pablo practice --suite-seeds --out ~/pablo-data/lessons-suite --n 60`
   (ledger, textkit, inventory; suite-named functions skipped). Report the kept rate and wall time.
4. Three arms, same commit and judge, `--repeat 3 --write` each:
   - baseline: `uv run pablo eval --repeat 3 --write` (reuse M7d's no-spec arm if the commit matches)
   - examples: `uv run pablo eval --learn examples --lessons ~/pablo-data/lessons-suite/lessons --repeat 3 --write`
   - examples + failures: `... --learn examples,failures ...`
   Keep a piece only if its mean beats the baseline by more than the spread and false ready doesn't go
   up (the M6 rule). Add a `docs/decisions.md` row with the numbers, then set the winners to `true`
   in `config/lanes.yaml`.
5. Routing stays off. It needs real outcomes: after a couple of weeks of use and practice, paste the
   `pablo lessons` outcome table.
6. Nightly practice: set `practice.repos` to one or two of your own repos and
   `practice.nightly_tasks: 20`. The next morning, `pablo lessons` should show new practice examples,
   and the Agents page should have shown practice waiting while anyone was active.
7. Phase 2 groundwork, measurement only: how long a training window fits on the 3090? Run
   `scripts/train_lora.py` on a few rows with `PABLO_TRAIN_MAX_SEQ` at 1024, 2048, and 4096
   (gradient checkpointing on). Record peak VRAM and seconds per step. Don't promote anything.
8. `uv run ruff format src tests` and confirm CI passes.

**Don't**
- Don't practice on `evals/suite/` or point `practice.repos` at it.
- Don't put practice examples in `docs/` or the repo. They live in `~/.pablo/lessons` or the `--out`
  folder.
- Don't train on lessons yet. Phase 2 comes after step 7's numbers.
