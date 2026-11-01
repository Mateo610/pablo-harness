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
| M3 | Judge and deep engines + benchmarks | Judge lane on KTransformers (or best measured engine); `docs/benchmarks.md` compares engines on tok/s, VRAM, load time | todo |
| M4 | Lane swapping + resources | Swapper loads lanes on demand inside 24 GB; `/api/resources` reports VRAM/RAM; no OOM across 20 mixed runs | todo |
| M5 | Sandbox | Checks run in a no-network container; a diff that opens a socket fails its checks | todo |
| M6 | Context | Repo map + Serena symbol lookup replace `repo_context`; coder fixes a bug in a file the task never names | todo |
| M7 | Traces and evals | Traces mirrored to self-hosted Langfuse; Promptfoo suite of 30 tasks with a baseline score | todo |
| M8 | Learning loop | LoRA trained on `pablo export-training` output; promoted only if eval score beats baseline | todo |
| M9 | Specialist lanes | Media lane dispatches image and 3D jobs; results come back as evidence cards | todo |
| M9b | Voice lane | Local speech-to-text (Whisper/Canary class) behind `/api/voice`; hold-to-talk on the phone starts a run | todo |
| M10 | Ownership | Every model archived with checksums; full offline run with network unplugged; Headscale option documented | todo |

## Core track (built with Claude, pushed to this repo)
| # | Milestone | Gate | Status |
|---|---|---|---|
| C0 | Harness core + cockpit scaffold | 23 tests pass, mypy strict, cockpit builds, demo run approvable | done |
| C1 | Terminal client (Textual) | `pablo tui` shows inbox, plan, live status; approve/reject by key | done |
| C2 | Phone remote (PWA) | Installable on phone over Tailscale; approve, reject, hold to talk | done (voice waits on the voice lane) |
| C3 | Overnight queue + morning brief | Tasks queued for the deep lane run overnight; brief summarizes results at 7:30 | done (runs inside `pablo serve`; real overnight test after M3) |
| C4 | Hunk review, replay, compare | Accept per hunk; replay a run step by step; same task on two lanes side by side | done |
| C5 | v0.1 public release | README with demo GIF, architecture diagram, install guide; repo flipped public | todo |

## Order that works
M0 → M1 unblocks everything real. C1–C2 can be built any time (fake mode). M3 and M4 go
together because the judge and coder cannot both stay resident in 24 GB. M8 waits for M7: no
training without an eval gate. C5 after M1–M5 so the public demo runs on real models.
