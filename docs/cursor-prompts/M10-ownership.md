# M10 · Ownership: nothing can be taken away

**Goal.** Pablo runs with the internet unplugged, every dependency is archived with checksums, and
remote access doesn't depend on a company that can revoke it.

**Steps**
1. Audit: every model has an archive entry + SOURCE.txt + THIRD_PARTY row; every engine build has its
   source tarball and pinned tag in `$PABLO_DATA/archive/engines/`; Python and npm deps are vendored
   (`uv` cache export, `npm pack` or an offline mirror) so a rebuild works offline.
2. Offline drill: unplug network (or block egress with nftables), restart Pablo, run the M7 eval
   suite. Everything except the escalation lane must work. Write `docs/offline.md`.
3. Second copy: document copying `$PABLO_DATA/archive` to an external HDD (preferred over portable
   SSDs for cold storage) and verifying with `sha256sum -c`. Add `scripts/verify-archive.sh`.
4. Headscale: document (and optionally test) a self-hosted Headscale control server so remote access
   doesn't depend on Tailscale Inc. Keep Tailscale as default; this is the exit plan.
5. Grep the repo and running services for telemetry/analytics endpoints; disable any found.

**Acceptance**
- Offline drill passes. Archive verifies. `docs/offline.md` exists. BUILD_PLAN M10 done.
