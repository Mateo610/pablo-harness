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

Matt sends you an invite. It has one command: paste it into Terminal (Mac, Linux) or
PowerShell (Windows). It installs Tailscale if you don't have it, has you accept access to
Pablo, then opens Pablo in your browser. Bookmark that page; that's it.

You get your own conversations and memory, and you see the team's runs. Only the owner applies
changes. Prefer the terminal? `uv tool install git+https://github.com/Mateo610/pablo-harness`,
then `pablo tui --url <the address you bookmarked>`. More in [docs/teammates.md](docs/teammates.md).

### Inviting someone (owner)

```bash
pablo invite --share-link <link> --note "Sam"   # first time; the share link is remembered
pablo invite --note "Alex"                      # after that
pablo invite --list                             # who used which invite
```

The share link is a reusable Tailscale share for this machine (admin console → Machines → pablo
→ Share → Copy invite link). Each `pablo invite` makes a one-time code that works for 7 days and
prints a message to send. Whoever opens it from the tailnet goes on `~/.pablo/access.yaml`.

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
