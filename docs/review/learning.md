# Learning

## What it does

Two phases. **Phase 2, weights:** `pablo learn` exports approved, trainable traces (never Claude,
never suite tasks, never legacy data), trains a LoRA on the coder, and promotes it only if it
beats the coder on 3 + 3 interleaved suite runs the same night. **Phase 1, lessons (now):**
`lessons.py` keeps examples from whole approvals and verified practice fixes, notes on what broke
before, and per-repo outcomes by task type. Nightly practice makes checkable tasks from your repos
(blank out a tested function, or plant one bug the tests notice) and keeps only fixes that pass
the real tests.

## What we know

- **[measured]** The first LoRA candidate was held: 7 examples, 1 run.
- **[measured]** `MAX_SEQ` is 256 tokens (`scripts/train_lora.py`); longer OOM'd with expert LoRA.
  A real coding example (task + context + diff) is thousands of tokens, so training sees
  fragments.
- **[gap]** Lessons (`learn.examples`, `learn.failures`, `learn.routing`) are all off and
  unmeasured. Practice is off (`nightly_tasks: 0`).

## Weaknesses

- **[measured]** Weight training can't learn style or repo knowledge at 256 tokens. Phase 2 needs a
  measurement of what window fits (attention-only LoRA, smaller batch, gradient checkpointing).
- **[suspected]** Examples in the prompt cost context the coder needs for files (they shrink the
  file budget).
- **[suspected]** Practice tasks are easier than real ones (one function, one mutation), so they
  may teach the easy cases.

## Ideas

| Idea | How we'd know |
|---|---|
| M8b: practice on the seed repos (`--suite-seeds`), then eval with `--learn examples` and `--lessons` | suite, 3 runs vs tonight's baseline |
| Then `failures` alone, then both | same |
| Measure the largest `MAX_SEQ` that trains on the 3090 without OOM, at attention-only rank 8 | a number; then whether 1–2k-token examples change the gate |

## Open questions

- Is the right next step for learning a better retriever (what to show) rather than more data?
