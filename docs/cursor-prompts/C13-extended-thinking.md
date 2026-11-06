# C13 · Extended thinking for Base and Hybrid (Pablo side)

**Context.** Chat has an "Extended thinking" switch next to the model menu, shown for Base and
Hybrid only. When it's on, the reply, any change it starts, and every step of a plan it drafts
use `pablo/coder-think`: the same Qwen3.6-35B-A3B weights served with `--reasoning on`. Voice
never uses it (too slow to talk), unfiltered projects never use it, and the judge stays at
reasoning off (Matt's call, 2026-11-06). Config: `coder.thinking` in `config/lanes.yaml`, the
`coder-think` entry in `config/llama-swap.yaml`, the `pablo/coder-think` alias in
`config/litellm.yaml`.

## Steps

1. `git pull && uv sync && (cd web && npm ci && npm run build)`; reload llama-swap and the
   gateway; restart `pablo.service`.
2. **Check the flags take.** `coder-think` runs `scripts/llama-coder.sh PORT --reasoning on
   --alias coder-think`, so those come after the script's own `--reasoning off --alias coder`.
   Confirm llama-server uses the last one: `curl 127.0.0.1:8100/upstream/coder-think/props` (or the
   server log) shows reasoning on, and a test request returns `reasoning_content`. If the first
   value wins instead, give the script a `PABLO_CODER_REASONING` variable and use that.
3. **Make room.** Thinking shares the window with the prompt. Today `coder.thinking` is
   conservative for the 16k window (`max_tokens: 12000`, `context_tokens: 4000`). Try
   `-c 32768` on `coder-think` (KV is q8_0); if VRAM holds with nothing else loaded, set
   `context_tokens: 10500` and `max_tokens: 16000` in `coder.thinking`. Note the VRAM.
4. **Time it.** In Chat, Base with the switch on: one question about the repo, one small change
   ("add a docstring to X"), and one plan ("let's build …, plan it first"). Record seconds to the
   first word, total seconds, and whether the run came back ready. Same three with the switch off.
5. **Optional, a night with the GPU free:** `uv run pablo eval --cascade --think --repeat 3
   --write` (reported as `coder→deep (thinking)`), to see whether thinking changes the pass rate
   or false ready against the clean baseline at `2e20e05` (89% ± 7%, false ready 8).
6. Record a row in `docs/decisions.md` (flags, VRAM, window, timings) and update
   `docs/review/models.md`.
