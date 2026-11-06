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
- **[gap]** Nothing here has been measured on the real models. Tests use scripted replies.

## Weaknesses

- **[suspected]** The biggest unknown: does Qwen choose RUN, PLAN or just talk at the right times?
  The prompt asks; nothing checks.
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
| The chat-behavior set from evals.md (talk / RUN / PLAN) | accuracy per model; prompt changes measured against it |
| Spoken summaries of a diff in one sentence ("adds a threshold function and two tests") from the judge's summary | you approve by voice without opening the screen (observed) |
| Ask "apply it?" back when consent words are mixed with changes ("yes, but…") | fewer wrong applies (observed) |
| Time first-word latency per turn and log it | p50/p95 on Agents |

## Open questions

- Should voice read the plan aloud before building, step by step, the way a person would?
