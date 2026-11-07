# Pablo

**A private AI coding assistant that runs on one box we own.**

You ask in chat or by voice. Pablo picks a local model, makes the change in a scratch copy, runs
the tests and type checks in a sealed container, and has a judge model score how sure it is.
Nothing lands in your repo until you approve it. No cloud, no telemetry; Claude is only an
escalation you choose on purpose.

```
you ── chat · voice · terminal ── Pablo ── router (Qwen3.5 4B) ─┬─ Base           Qwen3.6 35B-A3B
                                    │                           ├─ Best           gpt-oss-120b
                                    │                           └─ No guardrails  Mistral Small 3.2 24B
                                    ├─ checks: diff → no-network container → tests, types
                                    └─ judge (Qwen3.8 27B): sure → ready · unsure → needs you
```

## How it does

Measured on our own hardware (one RTX 3090). Our eval sets, not public benchmarks:
[docs/review](docs/review/README.md) has the details and the weak spots.

| | Result |
|---|---|
| 36 coding tasks, scored by hidden tests: Base | **86%** ± 3 (3 runs) |
| Same tasks, Hybrid (router picks, judge, retry) | **89%** ± 7 (3 runs) |
| Same tasks, Best alone | **94%** (1 run, ~3.5 h) |
| Chat does the right thing (answer, change, or plan) on 22 messages | **20/22** typed, **18/22** spoken |
| Small tools built from an empty project in plan mode | **2/5** pass their acceptance test |
| Base speed | ~120 tokens/s, first word in 2–5 s |

## Use it (teammates)

You need to be on our tailnet. Matt adds your Tailscale login to Pablo's access list.

- **Browser:** open `http://pablo:8710`
- **Terminal:**
  ```bash
  uv tool install git+https://github.com/Mateo610/pablo-harness
  pablo whoami --url http://pablo:8710   # should print your login
  pablo tui --url http://pablo:8710
  ```

You get your own conversations and memory, and you see the team's runs. Only the owner applies
changes. More in [docs/teammates.md](docs/teammates.md).

## Everyday commands

| | |
|---|---|
| `pablo run "<task>"` | make a change: route, write, check, judge |
| `pablo approve / reject <run>` | your call; only approve touches the repo |
| `pablo chat` | talk in the terminal |
| `pablo tui` | the full cockpit in the terminal |
| `pablo queue add "<task>"` | overnight queue (runs 01:00–06:30) |
| `pablo brief` | what finished overnight and what needs you |
| `pablo doctor` | check the model gateway, GPU and disk |
| `pablo eval` · `eval-chat` · `eval-build` | rerun the numbers above |

`pablo --help` lists the rest.

## Running the server

Pablo is built for one Ubuntu box with a 24 GB GPU and 64 GB of RAM, on Tailscale.

```bash
bash scripts/bootstrap-pablo.sh --install   # checks GPU, CUDA, Tailscale; installs the basics
bash scripts/start-lanes.sh                 # model swapper + gateway as systemd user units
uv run pablo cert                           # HTTPS, so the browser allows the mic
uv run pablo serve --host $(tailscale ip -4)
```

Pablo only binds to 127.0.0.1 or the Tailscale IP, never every interface.
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) has the full setup and roadmap, and
[AGENTS.md](AGENTS.md) has the rules every change follows.

## Ground rules

- **Proof beside the claim.** Every change comes with its test results and the diff.
- **You're the merge button.** Models never write to your repo on their own.
- **Owned outright.** Weights are archived with checksums, it runs offline, and Claude's outputs
  are never used for training.

## License

Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY.md](THIRD_PARTY.md).
