# Working on pablo-harness

Instructions for any coding agent (Cursor, Claude Code, Pablo itself) working in this repo.

## What this is
A self-hosted harness that routes coding work across a team of local models on one box
(RTX 3090 24 GB, 64 GB RAM, 2 TB NVMe), verifies every diff, has a judge score certainty,
and leaves the merge decision to the human. Claude is an escalation lane, never automatic.

## Layout
- `src/pablo/` harness core (Python 3.11+, FastAPI, Typer)
  - `config.py` lanes from `config/lanes.yaml` · `router.py` lane choice · `gateway.py` LiteLLM + FakeBackend
  - `verify.py` diff → scratch copy → checks in a `sandbox.py` Sandbox (rootless Podman + gVisor on Pablo, local in tests); a failing check is re-run on the unedited copy so a verdict blames the diff only for what it made worse · `judge.py` certainty · `orchestrator.py` the run
  - `traces.py` JSONL traces + training export · `plan.py` GSD-style `.pablo/plan.yaml`
  - `api.py` HTTP + SSE for the cockpit · `cli.py` the `pablo` command
  - `client.py` HTTP client · `tui.py` terminal cockpit (Textual)
  - `overnight.py` overnight queue, scheduler (runs inside `pablo serve`), morning brief
  - `hunks.py` split diffs into hunks for partial approval (mirrored in `web/src/hunks.ts`)
  - `resources.py` GPU/VRAM/RAM snapshot · `swap.py` which lanes llama-swap has resident · `judge_eval.py` judge accuracy on `evals/judge` fixtures
  - `tasks.py` task type + quick/build/deep · `context.py` budgeted prompt (files picked by the symbols a task names, per-lane `context_tokens`) + skill cards (`skills/*.md`)
  - `edits.py` find-and-replace edit blocks → git-made diff (the coder's default edit format)
  - `evals.py` `pablo eval`: the 30-task suite in `evals/suite`, scored by hidden tests the model never sees
- `web/` cockpit (React + Vite + cmdk); desktop `App.tsx` + `RunView.tsx`, phone `Phone.tsx` (PWA), styled only with tokens in `web/src/tokens.css`
- `config/` lanes and the LiteLLM gateway · `scripts/` Pablo bootstrap and model archiving
- `docs/cursor-prompts/` one prompt per milestone, each with acceptance checks

## Commands
```
uv sync                       # install
uv run pytest -q              # tests (no GPU needed: FakeBackend)
uv run ruff check src tests && uv run mypy
uv run pablo serve --fake     # API + cockpit with scripted models
cd web && npm install && npm run dev   # cockpit dev server on :5173
```

## Rules that are not negotiable
1. **Never train on Claude.** Traces from the `claude` lane are never `train_ok`. Do not weaken
   `Lane._remote_never_trains`, `Settings._name_lanes`, or `Trace.train_ok`. Tests guard this.
2. **The human is the merge button.** Nothing writes to the working tree except `Orchestrator.approve`.
   Partial approvals (`approved_partial`) are re-checked first and are never training data.
3. **Never auto-escalate.** The router must not choose `claude`; only `escalate` may.
4. **Bind locally.** Serve on 127.0.0.1 or the Tailscale IP. Never 0.0.0.0, never a public port.
5. **Licenses.** MIT/Apache/BSD code may be copied with its notice (add it to THIRD_PARTY.md).
   AGPL/GPL tools run as separate processes over a network or CLI boundary, never imported.
   Non-commercial or unlicensed code is not copied. Model weights are never committed.
6. **No phoning home.** No telemetry, no analytics, no CDN fonts. Check new dependencies for this.
7. **Evals never train.** `evals/suite` tasks, their hidden tests, and reference solutions never go
   into prompts, traces, or training data. `pablo eval` runs in a throwaway home. Don't show a model
   `solution` or `hidden`, and don't tune a prompt on the suite's hidden tests.
8. **No legacy data.** Never import anything from the old ai-coding-team `data/` folder into traces or
   training; some of it came from automatic Claude routing.

## Style
- Python: typed (`mypy --strict` passes), small modules, dataclasses/pydantic, no global state.
- Every new behavior gets a test that runs without a GPU (use `FakeBackend`).
- Web: tokens only (no raw hex outside tokens.css), amber (`--accent`) means "your move" and
  nothing else, real `<button>`/`<label>`, keyboard reachable, contrast ≥ 4.5:1.
- Keep the plan honest: when you finish a milestone, update `docs/BUILD_PLAN.md` status.
