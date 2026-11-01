# Pablo

**A private cockpit for a team of local AI models.**

Pablo routes each coding task to the model that's best at it, runs every diff through real checks in a
scratch copy, has a judge model score how sure it is, and puts the result in front of you with the
evidence beside it. You stay the merge button. Everything runs on one box you own; Claude is an
escalation lane you call on purpose, and its outputs are never used for training.

```
 you ── cockpit (web · terminal · phone) ── harness API ── router ─┬─ coder   (Qwen3.6 35B-A3B)
                                                │                  ├─ deep    (gpt-oss-120b, overnight)
                                                │                  ├─ media   (image · 3D · video)
                                                │                  └─ claude  (escalate only)
                                                ├─ verifier: diff → no-network container → tests, types
                                                ├─ judge: certainty ≥ 0.75 → ready, else "needs you"
                                                └─ traces → approved only → LoRA → eval gate
```

## Status
Early. The harness core, API, CLI, web cockpit, terminal client, and phone app work today with scripted models (`--fake`).
Real models arrive milestone by milestone: see [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

## Try it (no GPU needed)
```bash
git clone https://github.com/Mateo610/pablo-harness && cd pablo-harness
uv sync
cd web && npm install && npm run build && cd ..

mkdir -p /tmp/hello && git -C /tmp/hello init && uv run pablo init -C /tmp/hello
uv run pablo serve --fake -C /tmp/hello      # open http://127.0.0.1:8710
```
Or from the terminal: `uv run pablo run "add a demo note" --fake -C /tmp/hello`, then
`pablo approve <run-id>`.

## Commands
| | |
|---|---|
| `pablo init` | create `.pablo/plan.yaml` (phases, tasks, gates) |
| `pablo plan` | show the plan and the next task |
| `pablo run "<task>"` or `pablo run 1.2` | route → work → verify → judge |
| `pablo approve / reject / escalate <run>` | your decision; only approve touches your repo |
| `pablo hunks <run>` · `pablo approve <run> --hunks 0:0,1:0` | keep only some hunks (re-checked before applying; never training data) |
| `pablo compare "<task>" --lanes coder,deep` | same task on two lanes; approving one rejects the other |
| `pablo replay <run>` | every step of a run, in order |
| `pablo serve` | API + cockpit on 127.0.0.1 (put `tailscale serve` in front for HTTPS) |
| `pablo tui [--url …]` | the cockpit in your terminal, local or over Tailscale |
| `pablo queue add "<task>"` / `list` / `cancel` / `run` | overnight queue: slow work runs 01:00–06:30 |
| `pablo brief [--make]` | the morning brief: what finished overnight and what needs you |
| `pablo doctor` | gateway, GPU, disk |
| `pablo eval-judge [--set holdout] [--write]` | score the judge on fixed good/bad diffs (tune on `main`, report `holdout`) |
| `pablo export-training` | approved, trainable traces as chat JSONL |
| `pablo export-traces` | daily TOON stats files (no prompts or code) |

## Principles
- **Proof beside claim.** Every answer comes with test results, type checks, and the diff.
- **Amber means your move.** The UI only asks for attention when a decision is yours.
- **Local first, owned outright.** Weights archived with checksums, no telemetry, runs offline.
- **Escalation is explicit.** The router can't pick Claude; only you can.

## License
Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY.md](THIRD_PARTY.md).
