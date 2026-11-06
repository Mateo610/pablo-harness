# M8c · How long a training example fits on the 3090?

**Context.** The LoRA trainer (`scripts/train_lora.py`) cuts every example to
`PABLO_TRAIN_MAX_SEQ` tokens, 256 today, because longer runs ran out of memory with expert LoRA.
A real coding example (task, the files it needed, the diff) is 2–10k tokens, so today the
adapter learns from fragments, and phase 2 of learning (weights) can't work until this number is
much bigger. This prompt only measures; it doesn't promote anything.

## Steps

1. `git pull && uv sync`. Stop `pablo.service` and llama-swap so the GPU is empty (note what you
   stopped; start them again at the end). Matt should know Pablo is offline while this runs.
2. For each setup below, try `PABLO_TRAIN_MAX_SEQ` = 512, 1024, 2048, 4096, 8192 (stop going up at
   the first out-of-memory) on the existing training export, for 20 steps only:
   - today's setup (expert LoRA)
   - attention-only LoRA, rank 8
   - attention-only LoRA, rank 8, gradient checkpointing on, batch 1
   If the script has no switch for one of these, add an environment variable for it in
   `scripts/train_lora.py` (keep today's behavior the default) and say so.
3. For each run record: peak VRAM (`nvidia-smi --query-gpu=memory.used --format=csv -l 1` in a
   second terminal), seconds per step, and whether it finished.
4. Record one row in `docs/decisions.md` with the table, and the largest window that trains
   with headroom (under ~22 GB peak). Update `docs/review/learning.md`.
5. Start everything you stopped and check `systemctl --user is-active pablo.service`.

## Report back

The table, the largest safe window per setup, and the seconds per step at that window.
