# Chat, voice and plans

## What it does

Chat: a conversation per topic with memory (notes you keep), recall of earlier conversations,
and a model menu (Base, No guardrails, Best, Hybrid, Claude). The reply streams; a `RUN:` line
starts a change; `PLAN:`/`STEP:` lines draft a plan; `BUILD: go` builds it a step at a time;
`APPLY:`/`DISCARD:` decide the waiting change, only when your own words say so. Voice is the same
turn spoken: whisper.cpp in, Qwen3-TTS out, sentence by sentence, with spoken news when a run
finishes.

## What we know

- **[measured]** Voice prompts keep the same system prompt every turn so llama.cpp reuses its
  cache; plan state rides ahead of your message for the same reason.
- **[measured]** Hybrid chat (coder, reasoning off) on the 22-message set at `2563ea6`. Typed
  14/22: talk 3/8, run 5/8, plan 6/6. Spoken 16/22: talk 2/8, run 8/8, plan 6/6. Confusion
  (rows are what we wanted, columns are talk / run / plan):

  | | talk | run | plan |
  |---|---|---|---|
  | typed talk | 3 | 4 | 1 |
  | typed run | 0 | 5 | 3 |
  | typed plan | 0 | 0 | 6 |
  | spoken talk | 2 | 6 | 0 |
  | spoken run | 0 | 8 | 0 |
  | spoken plan | 0 | 0 | 6 |

  Wall 71 s typed, 33 s spoken. Runs were recorded, not started.
- **[measured]** Plan mode on 5 empty projects at `90319fc` (`pablo eval-build`). Hybrid planned
  5/5 on the first ask, finished 5/5, mean 3.8 steps. Acceptance passed 1/5 (`build-ratelimit`).
  Seven steps were approved with clean checks while the judge was unsure. None were rejected.
  Wall 1059 s, almost all of it `build-mdtoc` (796 s).

## Weaknesses

- **[measured]** A finished plan is usually a tool that fails its acceptance test: 4 of 5 at
  `90319fc`. The plan itself was written and built every time.
- **[measured]** Questions start work. Talk is 3/8 typed and 2/8 spoken; the rest became a run
  (one typed question became a plan). Spoken run and both plan columns are 8/8 and 6/6. Typed
  run is 5/8; the other three became plans.
- **[suspected]** Consent is a word list (`apply`, `yes`, `looks good`, …). "Yes, but change the
  name first" counts as yes. Safe enough while only you can apply, and the model must also write
  `APPLY:`, but crude.
- **[observed]** Spoken news says "Step 2 … is ready" but not *what changed*. On a voice call you
  can't see the diff, so you approve on trust in the checks and judge.
- **[gap]** Voice latency (first word after you stop talking) is unmeasured on Pablo.
- **[gap]** The phone layout is the old cockpit.

## Ideas

| Idea | How we'd know |
|---|---|
| Tighten the chat prompt so a question stays talk, on this same 22-message set | typed talk above 3/8 and spoken talk above 2/8, without losing the 6/6 plans or the spoken 8/8 runs |
| Spoken summaries of a diff in one sentence ("adds a threshold function and two tests") from the judge's summary | you approve by voice without opening the screen (observed) |
| Ask "apply it?" back when consent words are mixed with changes ("yes, but…") | fewer wrong applies (observed) |
| Time first-word latency per turn and log it | p50/p95 on Agents |

## Open questions

- Should voice read the plan aloud before building, step by step, the way a person would?
