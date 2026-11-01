# Cursor prompts

One file per Pablo-track milestone. How to use them:

1. On your laptop, open Cursor → Remote-SSH → `mateo610@<Pablo's Tailscale IP>` → open `~/pablo-harness`.
2. Start a **new Agent chat** for each milestone (fresh context works better than one long chat).
3. Paste the whole prompt file, or type `@docs/cursor-prompts/M1-gateway-and-first-lanes.md do this`.
   `.cursor/rules/pablo.mdc` and `AGENTS.md` load automatically.
4. Let it work, but **you** run anything with `sudo` and anything that downloads more than ~20 GB,
   so you can see it happen.
5. When the **Acceptance** commands all pass, commit:
   `git add -A && git commit -m "M1: gateway and first lanes" && git push`
6. If it gets stuck twice on the same error, stop, copy the error, and bring it back to Claude.

Model names and versions in these prompts reflect research as of Oct 2026. Every prompt tells
the agent to confirm the current repo, license, and quantization before downloading.
