# GPU: one card, many people

## What it does

Every GPU model call takes a lease from the scheduler (`gpu.py`): voice > chat > runs > retries >
night; calls for the loaded model share it; background work waits for quiet in Balanced mode.
Modes: Interactive, Balanced, Deep Work. Every Pablo process (the server, a CLI eval, practice)
shares one lease table. The TTS server is a resident: paused before Best loads, woken (after
unloading an idle Best) on a voice turn. Evals hold Deep Work and check every model first.

## What we know

- **[measured]** Without residents, Best returned 500 within ~9 s on 12 tasks: the voice server
  held VRAM Best needs. That cost two eval runs.
- **[measured]** Coder ↔ judge swap 7.6 s; Best load is minutes.
- **[measured]** Voice wake on 2026-11-07. `pablo run --lane deep "say hi"` stopped `pablo-tts` as soon as deep started (deep process 22734 MiB, card 23109). The run then loaded the judge (16784 MiB) and left TTS down. The next voice turn said "waking the voice…". TTS answered at 7.3 s, the coder was listed at 7.7 s, the first token at 18.4 s, and the first audio at 22.9 s after the transcript. Deep was already gone, so that interval has no deep unload. `GET /unload` on llama-swap v269 (`9fbc47c`) returns 200 and drops every model, including the CPU router.
- **[observed]** A `pablo serve` that had already marked TTS running kept `tts_state: running` after a CLI deep run stopped the unit. The Voice page then said "text-to-speech isn't answering" and disabled the orb. Restarting serve made the state `paused`, and the chip said the voice was sleeping.

## Weaknesses

- **[gap]** No timing under contention: how long does a chat wait behind a Hybrid retry on Best?
  The wake measured above started with the judge loaded, not with an idle Best still holding VRAM.
- **[suspected]** A lease can't stop a generation. A 30-minute Best call blocks a voice turn for
  30 minutes; the only answer today is "don't use Best during a call".
- **[suspected]** Cross-process ordering uses wall-clock age, local ordering uses who was served
  least recently. Two people in different processes aren't treated as fairly as in one.

## Ideas

| Idea | How we'd know |
|---|---|
| Record wait time per lease (asked → granted) and show p50/p95 per urgency on Agents | the numbers themselves; then whether changes help |
| Cancel a Best call that's still in its first minute when a voice turn arrives (llama-server can abort a request) | voice wait p95 |
| Keep a small CPU fallback TTS so voice never waits on the GPU | wake time → ~0, at some voice quality cost |

## Open questions

- Is Balanced's 120 s of quiet right for a household, or too eager / too shy?
