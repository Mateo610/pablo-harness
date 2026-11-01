# M8 · The learning loop

**Context.** `pablo export-training` writes approved, trainable traces (escalation traces are always
excluded, by code and by test). M7 gives a baseline eval score.

**Goal.** Nightly: if enough new approved examples exist, train a LoRA for the coder, evaluate it, and
promote it only if it beats the baseline. I approve promotions.

**Steps**
1. Training env in `$PABLO_DATA/engines/unsloth` (Apache-2.0; separate venv). Confirm it supports the
   coder base model in 24 GB; if not, train on the closest supported base or use QLoRA, and record it.
2. `scripts/train_lora.py`: input `training/approved.jsonl`, dedupe by prompt hash, hold out 10%,
   train, save adapter to `$PABLO_DATA/adapters/coder-<date>`. Refuse to run if any row's lane is
   `claude` (belt and braces).
3. Serve the adapter as a candidate lane `pablo/coder-candidate`, run `pablo eval` on it.
4. Write a "promotion" item to the inbox (new run state or a simple file the cockpit reads) with the
   score diff. Promote = swap the gateway alias; keep the previous adapter for rollback.
5. Systemd timer at 02:00, skip if fewer than 50 new examples.

**Acceptance**
- One full cycle runs end to end on real data; promotion is gated on eval score. BUILD_PLAN M8 done.
