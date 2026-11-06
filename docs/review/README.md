# Pablo, reviewed

Our own honest account of each part of the harness: what it's for, what we've actually measured,
where it's weak, and what we'd try next. `decisions.md` records what we chose; `benchmarks.md`
records raw numbers; this folder says what they add up to, area by area.

## How to use it

- **One page per area.** Each has the same five parts: *What it does*, *What we know* (measured,
  with the commit or decision it came from), *Weaknesses* (each tagged), *Ideas* (each with how
  we'd know it worked), and *Open questions*.
- **Evidence over opinion.** A weakness cites a run, a task id, a decision row, or a code path. If
  we only suspect it, it says *suspected* and what would confirm it.
- **Every idea names its measure.** Usually `pablo eval --repeat 3` against the latest clean
  baseline, sometimes a replay or a latency number. An idea we can't measure goes under *Open
  questions* until we can.
- **Update after anything that moves a number**: an eval, a bake-off, a real evening of use. Cursor
  prompts end with "update the review page for the area you touched".

Tags: **[measured]** a number says so · **[observed]** seen in use, not counted ·
**[suspected]** reasoning only · **[gap]** not built yet.

## Scorecard (2026-11-06)

| Area | Health | Biggest weakness | Next step |
|---|---|---|---|
| [Models](models.md) | measured on our suite | the base coder runs with thinking off at Q4; we never measured either choice | eval coder with thinking on, on a few hard tasks |
| [Evals](evals.md) | solid, small | 36 small tasks flatter local models; no held-out set | write 10–15 harder, multi-file tasks we never tune on |
| [Context](context.md) | measured, mixed | the coder sees ~10.5k tokens; the repo map didn't beat the spread | measure what the misses were missing |
| [Editing](editing.md) | strong | fix-on-red never cleared the noise floor | per-task view of what fix rounds change |
| [Verification](verification.md) | strong core, blind spot | checks pass when the coder writes a test that agrees with its own bug | spec checks with assertion-only failures, measured off-suite |
| [Judge](judge.md) | measured | 2 tasks no judge has ever caught; one clean Qwen run so far | tonight's clean baseline |
| [Routing and Hybrid](routing.md) | measured | the cascade's quality comes from deep, and deep is slow | learned routing on real outcomes |
| [Learning](learning.md) | built, unmeasured | LoRA window of 256 tokens can't hold a real example | measure lessons (examples, failures) on the suite |
| [GPU](gpu.md) | built, lightly tested | no latency numbers under contention; voice wake cost unknown | the evening checklist, then timings |
| [Chat, voice, plans](conversation.md) | built, untested on real models | nobody knows yet if Qwen writes PLAN vs RUN at the right times | evening checklist steps 2–4 |
| [Projects](projects.md) | built, untested | unfiltered projects run on a model never measured as a coder | eval the advisor model as a coder |

## The pattern so far

Three things keep showing up across areas:

1. **The harness moves the number more than the model.** Deep first failed to beat the coder
   (M4b) because of what it was shown, not what it was; after the context fix it scored 34/36.
   Fix-on-red, the repo map and two-step lookup each moved the mean by less than the spread.
   The cascade (+6 points) and better context were the real gains.
2. **Our blind spot is shared by every model.** `harness-memory-units` and `textkit-reading-time`
   fail almost every run, with every coder and every judge. The coder writes a test that agrees
   with its own wrong reading of the task, the checks pass, the diff reads fine. More model
   doesn't fix that; a check that comes from the task (not the diff) does.
3. **Our measurement is the bottleneck.** A 3-run suite has a spread of 2–7 points, so most
   single ideas can't show a win. Two of our recent runs were invalid for reasons outside the
   models (VRAM). Better evals (harder tasks, a held-out set, guarded runs) would make every
   other improvement easier to see.
