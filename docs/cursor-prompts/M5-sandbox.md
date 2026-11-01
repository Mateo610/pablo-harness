# M5 · Sandbox the checks

**Context.** Checks run model-written code. Until now they ran as plain subprocesses with Matt's
user, network, and home folder. The core track (Claude, 2026-11-01) already built the code side:

- `src/pablo/sandbox.py`: `Sandbox` protocol, `LocalSandbox` (tests, CI, `--fake`), and
  `ContainerSandbox`: one throwaway container per check with `--network none --read-only`, a tmpfs
  `/tmp`, `--cap-drop ALL`, `no-new-privileges`, pids/memory/CPU limits, only the scratch copy mounted
  at `/work`, and `--runtime runsc` (gVisor). It **fails closed**: no engine or no image means the
  check fails with "sandbox unavailable", never a fallback to the host. A timeout removes the
  container by name.
- `verify_diff(..., sandbox=)`; the orchestrator picks the sandbox from `lanes.yaml` `sandbox:` (set to
  `kind: container`, `engine: podman`, `runtime: runsc`). `--fake` always uses local.
- Per-project images: `pablo sandbox build -C <repo>` bakes the repo's dependency files (pyproject,
  uv.lock, requirements) into `pablo-check-<project>:<hash>`. Building is the only step with network,
  and only Matt runs it. A diff that changes dependencies gets a note in its verdict and is checked
  against the current image, never one built from the diff.
- `scripts/sandbox/Dockerfile` (base `pablo-check:py311`), `pablo sandbox status`, a sandbox line
  in `pablo doctor`.
- `tests/test_sandbox_container.py` (`@pytest.mark.container`): pass, no network, no DNS, writes
  outside `/work` never reach the host, the `/tmp/textutils.log` shell call from the judge fixture
  stays inside, memory limit, timeout cleanup. They skip unless the engine and image are ready.

**Why rootless Podman + gVisor, not AerolVM or Docker.** AerolVM (MIT, pre-v1) is a REST platform
with access tokens and Caddy on top of Docker plus optional gVisor. That's another server to secure
for something we can call directly. Docker needs either the `docker` group (root-equivalent: a
compromised harness owns the box) or rootless mode. Rootless Podman has no daemon and no root, and
a breakout lands as an unprivileged sub-UID. gVisor adds a user-space kernel, so model code never
talks to the host kernel directly. Record this in `docs/decisions.md` once it works on Pablo, or
record what broke and what you chose instead.

**Steps (Matt runs the sudo lines)**
1. `sudo apt install podman uidmap slirp4netns` (or `passt`). Check `podman info` shows
   `rootless: true`. Confirm subuid/subgid entries for `mateo610`.
2. Install gVisor from its apt repo (Apache-2.0; follow gvisor.dev install docs, verify the key).
   Register `runsc` for rootless Podman (`~/.config/containers/containers.conf`, `[engine.runtimes]`).
   Try `podman run --rm --runtime runsc --network none docker.io/library/python:3.11-slim python -c 1`.
   If runsc won't run rootless here, try `runsc --ignore-cgroups`; if it still fails, set
   `runtime: ""` (Podman's default crun, still rootless) and record why. Do not fall back to rootful.
3. Pin the base image by digest in `scripts/sandbox/Dockerfile`, then `uv run pablo sandbox build
   --base`. Pin pytest/mypy/ruff versions after the first build. `podman save` the image to
   `~/pablo-data/images/pablo-check-py311.tar` with a sha256 next to it.
4. `uv run pablo sandbox build -C ~/pablo-sandbox/hello`, then `uv run pablo sandbox status -C
   ~/pablo-sandbox/hello` must say ready.
5. `uv run pytest -m container -v`. All 7 must pass (not skip). If `--userns=keep-id` and runsc
   disagree, adjust `ContainerSandbox._user_args` (keep a unit test in `tests/test_sandbox.py`).
6. Real runs: `pablo run` three tasks on `~/pablo-sandbox/hello` with `pablo serve` up. Record check
   time per run, local vs container (gVisor startup cost), in `docs/benchmarks.md`. Run
   `uv run pablo eval-judge --set main` once to confirm nothing changed for the judge.
7. Optional, if quick: show `verdict.sandbox` and `verdict.notes` on the evidence card in
   `web/src/RunView.tsx` (tokens only, no amber).

**Acceptance**
- `uv run pytest -m container` passes on Pablo; `uv run pytest -q` still passes in CI (container
  tests skipped).
- A real `pablo run` on the sandbox repo shows `podman/runsc pablo-check-hello:...` in its verdict.
- Stopping Podman (or renaming the image) makes the next run's checks fail with "sandbox
  unavailable", and nothing runs on the host.
- `docs/decisions.md` entry; THIRD_PARTY.md rows for Podman (Apache-2.0) and gVisor (Apache-2.0) as
  `process`; BUILD_PLAN M5 marked done.
