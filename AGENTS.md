# Working on pablo-harness

Instructions for any coding agent (Cursor, Claude Code, Pablo itself) working in this repo.

## What this is
A self-hosted harness that routes coding work across a team of local models on one box
(RTX 3090 24 GB, 64 GB RAM, 2 TB NVMe), verifies every diff, has a judge score certainty,
and leaves the merge decision to the human. Claude is an escalation lane, never automatic.

## Layout
- `src/pablo/` harness core (Python 3.11+, FastAPI, Typer)
  - `config.py` lanes from `config/lanes.yaml` · `router.py` lane choice · `gateway.py` LiteLLM + FakeBackend
  - `verify.py` diff → scratch copy → checks · `judge.py` certainty · `orchestrator.py` the run
  - `traces.py` JSONL traces + training export · `plan.py` GSD-style `.pablo/plan.yaml`
  - `api.py` HTTP + SSE for the cockpit · `cli.py` the `pablo` command
  - `client.py` HTTP client · `tui.py` terminal cockpit (Textual)
- `web/` cockpit (React + Vite + cmdk); desktop `App.tsx`, phone `Phone.tsx` (PWA), styled only with tokens in `web/src/tokens.css`
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
3. **Never auto-escalate.** The router must not choose `claude`; only `escalate` may.
4. **Bind locally.** Serve on 127.0.0.1 or the Tailscale IP. Never 0.0.0.0, never a public port.
5. **Licenses.** MIT/Apache/BSD code may be copied with its notice (add it to THIRD_PARTY.md).
   AGPL/GPL tools run as separate processes over a network or CLI boundary, never imported.
   Non-commercial or unlicensed code is not copied. Model weights are never committed.
6. **No phoning home.** No telemetry, no analytics, no CDN fonts. Check new dependencies for this.

## Style
- Python: typed (`mypy --strict` passes), small modules, dataclasses/pydantic, no global state.
- Every new behavior gets a test that runs without a GPU (use `FakeBackend`).
- Web: tokens only (no raw hex outside tokens.css), amber (`--accent`) means "your move" and
  nothing else, real `<button>`/`<label>`, keyboard reachable, contrast ≥ 4.5:1.
- Keep the plan honest: when you finish a milestone, update `docs/BUILD_PLAN.md` status.
