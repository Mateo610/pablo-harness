# Projects

## What it does

Every git repo in `~/projects` is a project, plus the one `pablo serve` started in. The owner can
start a new one from Home (folder, git, README, `.pablo/project.yaml`). Each project has its own
orchestrator, checks and plan; all share the GPU, the inbox and the overnight queue. A project is
**standard** (all models) or **unfiltered** (the no-guardrails model does everything; no Best, no
Claude, never trained on).

## What we know

- **[gap]** Built and tested with fakes only (2026-11-06).

## Weaknesses

- **[gap]** The no-guardrails model has never been measured as a coder or a judge. Unfiltered
  projects may be much weaker, and it judges its own work.
- **[observed]** Since C14 the Advisor builds the same way in standard projects: an Advisor
  conversation gets `Workspace.advisor_orch` (the project's repo, unfiltered settings), so its
  runs and plan steps are `self_judged`, never retry on Base or Best, can't escalate, and never
  train. Same gap as above: nothing measured yet (tests only).
- **[suspected]** One plan per project. Two features at once in one project means one waits.
- **[suspected]** Lessons, the repo map cache and practice are keyed by folder name. Two projects
  with the same folder name in different places would share them.
- **[gap]** No way to archive or remove a project from Pablo's list except moving the folder.

## Ideas

| Idea | How we'd know |
|---|---|
| `pablo eval --lane coder` under `unfiltered_settings` (the advisor model as coder and judge) | pass rate and false ready next to the standard numbers |
| Key per-repo caches by path hash, not folder name | a test with two same-named repos |
| A "new project" template step: test runner, one test, a README section, before any feature | plan-mode eval |

## Open questions

- Should unfiltered projects get their own judge model (a second uncensored model) so the
  writer isn't grading itself?
