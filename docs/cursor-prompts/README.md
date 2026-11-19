# Cursor prompts

One file per Pablo-track milestone. How to use them:

1. On your laptop, open Cursor → Remote-SSH → `mateo610@<Pablo's Tailscale IP>` → open `~/pablo-harness`.
2. Start a **new Agent chat** for each milestone (fresh context works better than one long chat).
3. Paste the whole prompt file, or type `@docs/cursor-prompts/M9c-voice-latency.md do this`.
   `.cursor/rules/pablo.mdc` and `AGENTS.md` load automatically.
4. Let it work, but **you** run anything with `sudo` and anything that downloads more than ~20 GB,
   so you can see it happen.
5. When the **Acceptance** commands all pass, commit:
   `git add -A && git commit -m "M9c: voice latency" && git push`
6. If it gets stuck twice on the same error, stop, copy the error, and bring it back to Claude.

Model names and versions in these prompts reflect research as of Oct 2026. Every prompt tells
the agent to confirm the current repo, license, and quantization before downloading.

## Finished milestones

The prompts for milestones that are done (M0–M7d: baseline, gateway, porting, judge and deep,
lane swapping, deep reruns, sandbox, context, evals, judge bake-off, spec checks), and the M4
soak script, moved to the [`archive/unused-2026-11`](https://github.com/Mateo610/pablo-harness/tree/archive/unused-2026-11/docs/cursor-prompts)
branch on 2026-11-07. Their results live on in `docs/benchmarks.md` and `docs/decisions.md`.

