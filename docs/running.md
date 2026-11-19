# Running Pablo

Pablo is built for one Ubuntu box with a 24 GB GPU and 64 GB of RAM, on Tailscale. The reference
box is an RTX 3090, a Ryzen 7 5700X, 64 GB DDR4 and a 2 TB NVMe. Model weights and engines live in
`$PABLO_DATA` (default `~/pablo-data`), never in the repo.

## Start the server

```bash
bash scripts/bootstrap-pablo.sh --install   # checks GPU, CUDA, Tailscale; installs the basics
bash scripts/start-lanes.sh                 # model swapper + gateway as systemd user units
uv run pablo cert                           # HTTPS, so the browser allows the mic
uv run pablo serve --host $(tailscale ip -4)
```

Pablo only binds to 127.0.0.1 or the Tailscale IP, never every interface. Which model serves
which lane is in [config/lanes.yaml](../config/lanes.yaml) and
[config/llama-swap.yaml](../config/llama-swap.yaml); the paths in the swapper config and the
systemd units are the reference box's. [archive.md](archive.md) covers keeping a cold copy of
every weight and engine so the box can be rebuilt offline.

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
| `pablo eval` · `eval-chat` · `eval-build` · `eval-judge` | rerun the eval sets |

`pablo --help` lists the rest.

## Teammates

The owner sends an invite. It holds one command to paste into Terminal (Mac, Linux) or PowerShell
(Windows). It installs Tailscale if needed, has them accept access to Pablo, then opens Pablo in
their browser. Teammates get their own conversations and memory and see the team's runs; only the
owner applies changes. Terminal users can `uv tool install git+https://github.com/Mateo610/pablo-harness`
and then `pablo tui --url <the address they bookmarked>`.

```bash
pablo invite --share-link <link> --note "Sam"   # first time; the share link is remembered
pablo invite --note "Alex"                      # after that
pablo invite --list                             # who used which invite
```

The share link is a reusable Tailscale share for this machine (admin console → Machines → the
Pablo machine → Share → Copy invite link). Each invite is a one-time code valid for 7 days. How
identity, roles and the access file work is in [teammates.md](teammates.md).
