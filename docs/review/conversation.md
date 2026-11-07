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
- **[measured]** Spoken only, same 22 messages, at `b48581d`: 22/22, talk 8/8, run 8/8, plan
  6/6. Confusion (wanted → talk / run / plan): talk 8/0/0, run 0/8/0, plan 0/0/6. Wall 25 s.
  Against `bc535d9` spoken (18/22, talk 6/8, plan 4/6), the two talk misses and the two
  plans that became a single run are gone. Typed stays the `bc535d9` measurement.
- **[measured]** Plan mode again at `b48581d`: accepted 4/5 (`build-units`, `build-todo`,
  `build-ratelimit`, `build-mdtoc`), planned 5/5, finished 4/5, mean 2.8 steps, 3 steps
  rejected, 7 unsure, wall 5801 s. `build-csvstats` finished and failed. No project errored.
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
    and `pablo memory reindex`;
  - the same note could be suggested every turn and kept twice. Now a match (same words, or 80%
    of them) is never added again, a suggestion you rejected isn't made again, suggestions left
    14 days are set aside, and notes can be edited (`PATCH /api/memories/{id}`,
    `pablo memory edit`).

## Weaknesses

- **[measured]** Acceptance is 4 of 5 at `b48581d`, from 2 of 5 at `bc535d9` and 1 of 5 at
  `90319fc`. `build-csvstats` finished (3 steps, each approved, each unsure) and failed: the
  acceptance test could not import `csvstats`. `build-units` passed and was not marked finished
  (2 of its steps rejected).
- **[measured]** Spoken questions stay talk, and spoken plans stay plans, at `b48581d` (talk
  8/8, plan 6/6). At `bc535d9` spoken talk was 6/8 and 2 of 6 plans became a single run. Typed
  stays `bc535d9`: 20/22, and 2 of 8 questions started a run.
- **[suspected]** Consent is a word list (`apply`, `yes`, `looks good`, …). "Yes, but change the
  name first" counts as yes. Safe enough while only you can apply, and the model must also write
  `APPLY:`, but crude.
- **[observed]** Spoken news says "Step 2 … is ready" but not *what changed*. On a voice call you
  can't see the diff, so you approve on trust in the checks and judge.
- **[measured]** Voice first word on Pablo, 2026-11-07, coder and the voice server already up. Five orchestrator turns: 4.58, 4.69, 4.37, 4.10, 4.80 s (median 4.58, worst 4.80). Five advisor turns: 11.19, 7.15, 8.47, 5.76, 5.83 s (median 7.15, worst 11.19). The 2.5 s target is missed. On a warm orchestrator turn, speech-to-text is ~1.96 s and the first token ~0.30 s after that; speaking the one-word reply takes about 2 s more. Wake after a deep run, not part of the warm time: 22.9 s from the transcript to the first audio. The Voice page showed "waking the voice…".
- **[gap]** The phone layout is the old cockpit.
- **[suspected]** Typed chat put per-message parts (ranked notes, recall, repo context) in the
  system prompt, so llama.cpp re-read the whole prompt every turn. `chat.stable_prefix` moves them
  onto your message; off until timed.
- **[suspected]** Recall used to match any shared word and include the model's own replies. Now:
  your messages only, two shared words, halved every 30 days. The thresholds are guesses; no
  recall test measures them yet.
- **[gap]** Long chats drop old turns with no summary. A note that replaces another (`supersedes`)
  isn't modelled: edit the old note instead. The cockpit has no edit button yet (API and CLI do).

## Ideas

| Idea | How we'd know |
|---|---|
| Typed questions stay talk on this same 22-message set, and `build-csvstats` passes acceptance | typed talk 8/8 without losing run 8/8 (spoken is already 22/22 at `b48581d`); `build-csvstats` accepted |
| Spoken summaries of a diff in one sentence ("adds a threshold function and two tests") from the judge's summary | you approve by voice without opening the screen (observed) |
| Ask "apply it?" back when consent words are mixed with changes ("yes, but…") | fewer wrong applies (observed) |
| Log first-word latency on each turn and show it | p50/p95 on Agents; the 2026-11-07 sample is median 4.6 s, worst 4.8 s on the orchestrator |
| `chat.stable_prefix: true` | first-token latency on turn 2+ drops on Pablo; the chat set holds |
| A recall test: 20 questions with the earlier message they should find | hit rate and wrong hits, before tuning the two-word floor or the half-life |
| A rolling summary of the turns a long chat drops | a planning chat still knows its first decisions on turn 40 (observed) |

## Open questions

- Should voice read the plan aloud before building, step by step, the way a person would?
