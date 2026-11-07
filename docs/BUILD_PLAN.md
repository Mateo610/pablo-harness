# Build plan

Two tracks run side by side. **Pablo track** work needs the GPU and is done in Cursor connected to
Pablo over Remote-SSH, one milestone per chat, using the prompts in `docs/cursor-prompts/`.
**Core track** work needs no GPU and is built with Claude, then pushed here. Both meet at the
harness API, so neither blocks the other.

Status: `todo` · `doing` · `done`. Update this table when a gate passes.

## Pablo track (Cursor on Pablo)
| # | Milestone | Gate (must pass to close) | Status |
|---|---|---|---|
| M0 | Pablo baseline | `bootstrap-pablo.sh` reports ready; cockpit in `--fake` mode opens from your laptop over Tailscale and a demo run can be approved | done (phone install skipped) |
| M1 | Gateway + router and coder lanes | `pablo doctor` shows gateway up; `pablo run` makes a real diff in a scratch repo, checks run, judge scores it | done |
| M2 | Port the good parts of ai-coding-team | Complexity predictor and TOON export live under `src/pablo/` with tests; old folder archived read-only | done in core track (archive the old folder on Pablo: see M2 prompt step 6) |
| M3 | Judge and deep engines + benchmarks | Judge lane on KTransformers (or best measured engine); `docs/benchmarks.md` compares engines on tok/s, VRAM, load time | done (judge; deep is fit-math only, GLM-5.2 not downloaded) |
| M4 | Lane swapping + resources | Swapper loads lanes on demand inside 24 GB; `/api/resources` reports VRAM/RAM; no OOM across 20 mixed runs | done (coder and judge swap; deep waits on a model that fits) |
| M4b | Deep lane | gpt-oss-120b (or Qwen3.5-122B-A10B) joins the GPU swap group; bake-off vs coder on 6 hard tasks decides keep or drop | done (deep off: both models scored 1 better, 1 worse, 4 same) |
| M4c | Deep rerun with real context | Symbol-based file picking, per-lane prompt budget, before-and-after checks; harness checks green in the sandbox; gpt-oss bake-off rerun decides keep or drop | done (deep off the router, available by hand: 2 better, 2 worse, 2 same) |
| M5 | Sandbox | Checks run in a no-network container; a diff that opens a socket fails its checks | done (rootless runsc; pids, CPU pin, and the 4g address cap hold) |
| M6 | Context | Repo map (then Serena if needed) beats the 36-task suite's 3-run mean by more than its spread, false ready not up | done (not a keep: two-step 84% ± 2% vs baseline 82% ± 6%, false ready 7 > 4; map and lookup stay) |
| M7 | Evals, then traces | `pablo eval`: 30 tasks with hidden tests, coder baseline and noise floor recorded; deep rematch on the suite. Langfuse viewer is M7b, optional | done (coder v2 82% ± 6%; deep rematch 34/36 = 94%; Langfuse is M7b) |
| M7c | Judge bake-off | `pablo eval-judge --set replay` on real suite diffs labeled by hidden tests; new judge has fewer false ready than the heretic at its best threshold. Heretic becomes the `advisor` lane | done (bake-off at 18d5523; Matt picked Qwen3.8-27B dense, threshold 0.75; clean cascade at 2e20e05: 34, 29, 33 of 36, 89% ± 7%, false ready 8) |
| M7d | Task-derived checks | Tests written from the request before the diff is seen, run before/after in the sandbox; on only if false ready drops with ≤ 1 false alarm per run | done (not a keep: false ready 7 → 2 and pass 81% ± 7% → 83% ± 3%, but false alarms averaged 2 per run; stays off. Pass and false-ready numbers were hit by deep 500s, see decisions 2026-11-06) |
| M7e | Harder evals | Holdout (12 multi-file tasks, totals only), build set (plan mode end to end), chat behavior (talk/run/plan), Claude on the suite by hand | doing (built; run on Pablo: `docs/cursor-prompts/M7e-harder-evals.md`) |
| M8 | Learning loop | LoRA trained on `pablo export-training` output; promoted only if eval score beats baseline | doing (pipeline runs end to end; gate fixed 2026-11-04 to 3+3 same-night runs; first candidate held: 7 examples, 1 run) |
| M8b | Learning without retraining | Practice tasks from your repos (kept only if the real tests pass), examples and failure notes in the coder prompt, learned coder-vs-deep routing; each kept only if the suite says it helps | doing (built in core, off; `docs/cursor-prompts/M8b-learning.md`) |
| C10 | Projects and the model menu | Chat menu Base / No guardrails / Best / Hybrid by model name; projects listed and started from Home, each with its own orchestrator on one GPU; unfiltered projects on one model | done (`src/pablo/projects.py`, Home, Chat, Voice) |
| C11 | Plan mode | Talk a plan into `.pablo/plan.yaml`, then "go" runs its steps in order, each verified, stopping at your approvals | done (`src/pablo/plans.py`, PLAN/STEP/BUILD/APPLY/DISCARD lines, Plan card in Chat) |
| C12 | Build by voice | Voice works in a project, plans and builds like Chat, says when a step is ready, and applies or discards on your word | done (`/api/voice/news`, Plan card on Voice, APPLY/DISCARD) |
| C13 | Extended thinking switch | Chat switch for Base and Hybrid: the coder's thinking variant writes the reply, its change and its plan's steps; `pablo eval --think` | built; Pablo side in `docs/cursor-prompts/C13-extended-thinking.md` |
| C14 | Chat modes, Plan mode, skills, /loop | Auto and Plan (Minor, Major), a plan panel beside the chat, one plan per chat, needs-you cards that fix things, plans across chats in Work, slash-command skills, `/loop` | C14a, C14b built; C14c–g open (`docs/cursor-prompts/C14-chat-modes.md`) |
| M9 | Specialist lanes | Media lane dispatches image and 3D jobs; results come back as evidence cards | todo |
| M9b | Voice lane | Talk to the orchestrator, advisor, or deep in the browser: local speech-to-text and text-to-speech, replies spoken as they stream, turns land in a conversation, first word under 2.5 s on the orchestrator | doing (orchestrator first word median 4.6 s, worst 4.8 s, over 2.5 s: speech-to-text ~2.0 s and the first sentence's audio ~2 s. Advisor median 7.1 s, worst 11.2 s. Wake after deep 22.9 s transcript to first audio. Stock voices Vivian and Serena. HTTPS cert still needs one `sudo`. `docs/cursor-prompts/M9b-voice.md`) |
| M10 | Ownership | Every model archived with checksums; full offline run with network unplugged; Headscale option documented | doing (archive + verify script + Headscale doc + telemetry audit; offline drill after tonight's evals) |

## Core track (built with Claude, pushed to this repo)
| # | Milestone | Gate | Status |
|---|---|---|---|
| C0 | Harness core + cockpit scaffold | 23 tests pass, mypy strict, cockpit builds, demo run approvable | done |
| C1 | Terminal client (Textual) | `pablo tui` shows inbox, plan, live status; approve/reject by key | done |
| C2 | Phone remote (PWA) | Installable on phone over Tailscale; approve, reject, hold to talk | done (voice waits on the voice lane) |
| C3 | Overnight queue + morning brief | Tasks queued for the deep lane run overnight; brief summarizes results at 7:30 | done (runs inside `pablo serve`; real overnight test after M3) |
| C4 | Hunk review, replay, compare | Accept per hunk; replay a run step by step; same task on two lanes side by side | done |
| C5 | v0.1 public release | README with demo GIF, architecture diagram, install guide; repo flipped public | todo |
| C6 | Conversations + memory | Multi-turn chats per profile in `~/.pablo/pablo.db`; RUN lines start ordinary runs; notes kept only when you keep them; FTS search; nothing of memory goes to Claude | done (API + `pablo chat` / `pablo memory`; cockpit v2 Chat UI next) |
| C7 | Profiles + teammate access | Tailscale identity → profile, owner/member roles, only the owner approves | done (`tailscale whois` per peer IP; headers never trusted; `~/.pablo/access.yaml`; see `docs/teammates.md`) |
| C8 | Cockpit v2 in the browser | Home, Chat, Advisor, Agents live on the API; Voice shows the orb until M9b; v1 tools stay as Work | doing (pages live; voice lane, phone layout for the new pages, GPU mode switch to come) |
| C9 | GPU scheduler | One lease for the GPU: voice > chat > runs > retries > night; same-model calls share; background waits for quiet; Interactive / Balanced / Deep Work modes | done (`src/pablo/gpu.py`, `/api/gpu`, switch on Home, queue on Agents); residents: the TTS server pauses for deep and wakes on a voice turn; `pablo eval` holds Deep Work while it runs; one lease table shared by every Pablo process |

## Order that works
M0 → M1 unblocks everything real. C1–C2 can be built any time (fake mode). M3 and M4 go
together because the judge and coder cannot both stay resident in 24 GB. M8 waits for M7: no
training without an eval gate. M7's suite goes before M6 so context changes have a number to beat. C5 after M1–M5 so the public demo runs on real models.
