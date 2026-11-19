# Docs

## Design and evidence
- [review/](review/README.md): an honest account of each area (models, evals, context, editing,
  verification, judge, routing, learning, GPU, conversation, projects): what's measured, where
  it's weak, what's next. Every claim is tagged *measured*, *observed*, *suspected* or *gap*.
- [decisions.md](decisions.md): every design choice the milestones left open, with the reason and
  the alternatives considered.
- [benchmarks.md](benchmarks.md): raw numbers from every measured run, append-only.

## Plan
- [BUILD_PLAN.md](BUILD_PLAN.md): milestones, the gate each one has to pass, and status.
- [cursor-prompts/](cursor-prompts/README.md): the spec for each open milestone that runs on the
  GPU box, written for a coding agent, each with acceptance checks. Finished ones are on the
  [`archive/unused-2026-11`](https://github.com/Mateo610/pablo-harness/tree/archive/unused-2026-11)
  branch.

## Operations
- [running.md](running.md): starting the server, everyday commands, inviting teammates.
- [teammates.md](teammates.md): how Pablo knows who's asking, roles, and the access file.
- [archive.md](archive.md): the offline copy of weights, engines and caches.
- [headscale.md](headscale.md): running without Tailscale's coordination server.
- [telemetry.md](telemetry.md): the audit showing nothing phones home.
