# Teammates on Pablo

Pablo knows who's asking from Tailscale, not from a password. A teammate gets their own
conversations and memory, can start runs and talk to every local lane, and sees the team's runs.
Only you (the owner) can apply a change, escalate to Claude, promote an adapter, edit the plan, or
run the overnight queue by hand.

## How Pablo tells people apart

| Request from | Who it is |
|---|---|
| A tailnet device | Pablo asks `tailscaled` who owns the connecting IP (`tailscale whois`). WireGuard already proved the IP; nothing in the request can change the answer. |
| Your login (or, with no `access.yaml`, whoever owns the Pablo machine on the tailnet) | Owner |
| A login under `members` | That teammate |
| Anyone else, or a tagged device | Refused (403) |
| This machine (loopback, or Pablo's own Tailscale IP) | Owner while you work alone. Once `members` is set, it needs the owner token, because another account on this box is also "local". |

`Tailscale-User-*` headers are never trusted. Anything that reaches the port can send them.
A request that carries them (from `tailscale serve` or `funnel`) is refused with a message.
**Don't put `tailscale serve` or `funnel` in front of Pablo.** Bind it to its Tailscale IP.

## Set up

1. On Pablo, write `~/.pablo/access.yaml`. It lives outside the repo, because logins are personal.

   ```yaml
   owner: you@github          # your Tailscale login (`tailscale whois <your laptop ip>`)
   members:
     - friend@github
   loopback: auto             # auto: token only when members exist · owner · token
   hosts: [pablo]             # other names you open Pablo by, if any (see below)
   ```

   Pablo knows you by your address, so `/api` answers only names a browser could have used to
   reach Pablo itself: any IP, `localhost`, this machine's hostname, `*.ts.net`, the name
   `pablo cert` issued, and `hosts`. That stops a web page you have open from pointing its own
   domain at Pablo's IP (DNS rebinding) and acting as you. A request that changes something
   must also come from a page Pablo served. If the MagicDNS short name (`http://pablo:8710`)
   differs from the machine's hostname, list it under `hosts`.

2. Serve on the Tailscale IP:
   `pablo serve --host $(tailscale ip -4)`. It still refuses `0.0.0.0`.
3. Optional, recommended: in your tailnet policy, let teammates reach only the cockpit port, not
   SSH or anything else on Pablo:

   ```json
   "hosts": { "pablo": "100.x.y.z" },
   "grants": [
     { "src": ["friend@github"], "dst": ["pablo"], "ip": ["tcp:8710"] }
   ]
   ```

   If you tag the Pablo node, its owner becomes `tagged-devices`. Set `owner:` explicitly then.

4. Check it: `pablo whoami --url http://<pablo tailscale ip>:8710` from each device.

## Teammates connect

- Browser: `http://pablo:8710` (MagicDNS) or `http://<pablo tailscale ip>:8710`. The traffic is
  inside WireGuard.
- Terminal: install Pablo on their own machine (`uv tool install git+https://github.com/Mateo610/pablo-harness`),
  then `pablo tui --url http://pablo:8710`.

**A shell on Pablo is owner-level.** Don't give teammates shell accounts on Pablo. A shell under
your account can read `~/.pablo` and the repo. With `members` set, another account on the box gets
401 (no token) instead of passing as you.

## You, on Pablo itself

- `pablo token` prints the owner token and a link that signs this machine's browser in (a cookie).
- The TUI and `pablo whoami` on this machine read `~/.pablo/owner.token` (mode 0600) on their own.
