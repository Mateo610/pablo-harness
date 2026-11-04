# Headscale exit plan

**Default today:** Tailscale Inc.'s coordination server. Pablo binds to `127.0.0.1` or the
Tailscale IP only — never `0.0.0.0`. Phone and laptop reach the cockpit over the tailnet.

**Exit plan:** if Tailscale Inc. revokes access, raise prices unacceptably, or the control
plane is unreachable, run a self-hosted [Headscale](https://github.com/juanfont/headscale)
control server and point the same Tailscale clients at it. The wire protocol stays
WireGuard; only the coordination server changes.

This is documentation and an optional drill — **do not** switch Pablo's day-to-day access
off Tailscale until you have tested a cutover on a spare node.

## What Headscale replaces

| Piece | Stays | Changes |
|---|---|---|
| `tailscaled` on Pablo, phone, laptop | yes | config: login server URL |
| WireGuard data plane | yes | same |
| ACLs / tags | recreate in Headscale | export/import as needed |
| MagicDNS names | optional in Headscale | pick a base domain you control |
| Tailscale's SaaS coordination | — | replaced by your Headscale |

Pablo's bind rules do not change: still `127.0.0.1` or the tailnet IP from `tailscale ip -4`.

## Minimal cutover (when you need it)

1. **Host Headscale** on a cheap VPS you control (or a second box at home with a stable name).
   Prefer a pinned release binary or distro package; record the version in
   `$PABLO_DATA/archive` the same way as other engines. License: BSD-3-Clause.
2. **Create a user / namespace** in Headscale for the household (see upstream `headscale users create`).
3. **Pre-auth keys** (or the OIDC flow Headscale supports) for Pablo, the phone, and the laptop.
4. **On each client**, point at Headscale instead of `controlplane.tailscale.com`:

   ```bash
   sudo tailscale logout
   sudo tailscale up --login-server=https://headscale.example.com --authkey=tskey-...
   ```

5. **Confirm** `tailscale status` shows the peers, Pablo's cockpit still answers on the
   new tailnet IP, and nothing is listening on a public interface (`ss -ltnp` / the
   existing bind checks).
6. **Keep a Tailscale backup path** until the Headscale tailnet has been the daily driver
   for a while. Do not delete the Tailscale account on day one.

## What we are not doing yet

- Not running Headscale on Pablo itself as the only control server (a house outage would
  lock you out of the house). Prefer a small external VPS for coordination.
- Not committing Headscale config or auth keys to this repo.
- Not changing `pablo serve` bind defaults.

## License / ownership note

Headscale is BSD-3-Clause. Run it as a separate process. Add a `THIRD_PARTY.md` row and an
archive entry when you actually deploy it — not before.
