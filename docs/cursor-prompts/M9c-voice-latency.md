# M9c · Voice latency: get the first word under 2.5 s

**Context.** M9b measured the orchestrator's first audio at a median of 4.58 s (worst 4.80 s) on
a 2.48 s clip whose answer was one word (decisions.md, 2026-11-07). The model is not the slow
part (first token ~0.30 s after the transcript). Speech-to-text took ~1.96 s, and the first
sentence's audio took ~2 s. Speech costs time per word: a 12-word sentence took 7.6–8.7 s to
come back whole. So a real answer, whose first sentence runs 10–20 words, starts much later
than the one-word sample suggests.

Already changed in the harness (`voice.SentenceCutter`, tests in `tests/test_voice.py`): the
first thing said now ends at its first clause (a comma, colon or dash after 4 words) or its first
sentence, however short. Later pieces are still whole sentences.

Also changed: after a voice wake unloads llama-swap, Pablo asks for the router again in the
background (`gpu.keep_warm: [router]`), because `/unload` drops it too.

**Rules.** Same as M9b: local processes on 127.0.0.1, pinned versions, weights archived, licenses
checked, no telemetry. Record every number in `docs/benchmarks.md` and `docs/decisions.md`, and
update `docs/review/conversation.md`. Don't change two things at once between measurements.

**Clock and clips.** Same clock as M9b: from the voice-turn request to the first audio event the
page plays, five turns each, coder and TTS already loaded. Use two clips:
- A: the M9b clip ("What is two plus two?"), for comparison.
- B: a question whose answer is a few sentences, e.g. "What does the ledger module do, and what
  would you change first?" Delete clips after the run, as before.

**Steps**
1. `git pull`, `uv sync`, `cd web && npm ci && npm run build`, restart `pablo.service`.
   Measure A and B with no other change. This is the new baseline (the first-clause cut is in).
2. Router warm. Run `pablo run --lane deep "say hi"` (reject it), then a voice turn, then a typed
   Chat turn on Hybrid. `GET http://127.0.0.1:8100/running` right after the voice wake should list
   the router within ~10 s. Record the typed turn's first token against a warm one.
3. Speech-to-text, one change at a time, each on clips A and B:
   a. `whisper-server` built with CUDA, `small.en` on the GPU. Check VRAM with coder + TTS +
      whisper (the card had ~1.7 GB free with coder + TTS). If it doesn't fit beside the coder,
      stop here and note it.
   b. `base.en` on the CPU (cores 4–7 as now). Note any code words it gets wrong
      ("rename load_line in ledger"). Faster but wrong transcripts are not a keep.
   c. Threads: `-t 4` against `-t 8` with `CPUAffinity=4-7,12-15` (the sandbox's cores), only if
      no checks run during voice. Probably not a keep; measure once.
   Keep the fastest option that transcribes the code-word clip right.
4. Text-to-speech:
   a. Check whether the Qwen3-TTS server can stream audio for one request (chunked response or
      `stream: true` on `/v1/audio/speech`). If it can, measure time to its first audio bytes
      against the whole wav. Don't change the harness yet; record what the server offers.
   b. FlashAttention for Qwen3-TTS (M9b skipped it). Build it only if a prebuilt wheel matches
      the CUDA and torch versions on Pablo. Measure the 12-word sentence again.
5. Write the result: median and worst first audio for A and B, on the orchestrator and the
   advisor, and what you kept.

**Gate.** The orchestrator's median first audio is under 2.5 s on clip B, the code-word clip
transcribes right, and VRAM with coder + TTS (+ whisper, if it moved) stays under 23.5 GB.
If the gate isn't met, say which step got closest and by how much.
