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
- **[measured]** Same 22 messages at `bc535d9`. Typed 20/22: talk 6/8, run 8/8, plan 6/6
  (confusion talk 6/2/0, run 0/8/0, plan 0/0/6). Spoken 18/22: talk 6/8, run 8/8, plan 4/6
  (talk 6/2/0, run 0/8/0, plan 0/2/4). Against `2563ea6`, talk rose from 3/8 and 2/8, typed run
  from 5/8 to 8/8, and spoken plan fell from 6/6 to 4/6.
- **[measured]** Plan mode on 5 empty projects at `90319fc`: accepted 1/5, mean 3.8 steps, wall
  1059 s, nothing rejected. At `bc535d9`: accepted 2/5 (`build-units`, `build-ratelimit`),
  planned 5/5, finished 4/5, mean 3.4 steps, 4 steps rejected, wall 3873 s. No project errored.

## Weaknesses

- **[measured]** A finished plan still usually fails its acceptance test: 2 of 5 at `bc535d9`,
  up from 1 of 5 at `90319fc`. `build-csvstats` stopped after two rejected steps.
- **[measured]** A question still starts a run 2 times out of 8, typed and spoken (`bc535d9`).
  That was 4 of 8 typed and 6 of 8 spoken at `2563ea6`. Spoken turns 2 of 6 plan requests into
  one run.
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
| Keep a question as talk, and a spoken plan as a plan, on this same 22-message set | typed and spoken talk above 6/8, spoken plan back to 6/6, without losing run 8/8 |
| Spoken summaries of a diff in one sentence ("adds a threshold function and two tests") from the judge's summary | you approve by voice without opening the screen (observed) |
| Ask "apply it?" back when consent words are mixed with changes ("yes, but…") | fewer wrong applies (observed) |
| Time first-word latency per turn and log it | p50/p95 on Agents |

## Open questions

- Should voice read the plan aloud before building, step by step, the way a person would?
