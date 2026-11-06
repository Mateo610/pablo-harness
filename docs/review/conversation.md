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
- **[observed]** Memory review (2026-11-06, branch `memory-fixes`) found and fixed, each with a
  test in `tests/test_convos.py`:
  - notes were appended to the run's *task*, so escalating sent them to Claude, and teammates
    (`/api/runs`), lessons, routing and traces all saw them. They now ride in `Run.notes`, reach
    local coders only, and stay out of every API answer;
  - past 12 kept notes, ranking ignored scope (another project's notes came back), and recall
    searched every project;
  - chat could only save profile notes: now `remember for this project: …` and
    `REMEMBER (project): …`;
  - any message starting "remember that…" became a note without reaching the model (questions
    included);
  - a note and its search row were separate commits; now one transaction, a self-check on open,
    and `pablo memory reindex`.

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
- **[suspected]** Typed chat put per-message parts (ranked notes, recall, repo context) in the
  system prompt, so llama.cpp re-read the whole prompt every turn. `chat.stable_prefix` moves them
  onto your message; off until timed.
- **[suspected]** Recall matches any shared word and includes the model's own replies, so an old
  wrong answer can come back as "from earlier conversations". No recency weight.
- **[gap]** Notes have no dedupe, edit or supersede; long chats drop old turns with no summary.

## Ideas

| Idea | How we'd know |
|---|---|
| The chat-behavior set from evals.md (talk / RUN / PLAN) | accuracy per model; prompt changes measured against it |
| Spoken summaries of a diff in one sentence ("adds a threshold function and two tests") from the judge's summary | you approve by voice without opening the screen (observed) |
| Ask "apply it?" back when consent words are mixed with changes ("yes, but…") | fewer wrong applies (observed) |
| Time first-word latency per turn and log it | p50/p95 on Agents |
| `chat.stable_prefix: true` | first-token latency on turn 2+ drops on Pablo; the chat set holds |
| Recall from your own messages only, with a minimum score and recency decay | a small recall test: right hits up, wrong ones down |
| Dedupe proposals against kept notes; `supersedes` for a note that replaces one | proposals waiting on Home stop repeating (observed) |

## Open questions

- Should voice read the plan aloud before building, step by step, the way a person would?
