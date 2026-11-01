# M5 · Sandbox the checks

**Context.** `verify.py` applies diffs to a scratch copy and runs checks as plain subprocesses. Model-
written code then runs with my user's permissions and network. Fix that.

**Goal.** Checks run in a locked-down container: no network, read-only base image, CPU/memory/time
limits, only the scratch copy mounted.

**Steps**
1. Evaluate AerolVM (MIT, microVM sandbox) vs plain Docker with `--network none --read-only
   --pids-limit --memory --cpus` and a tmpfs. Choose; record why in `docs/decisions.md`. Docker is an
   acceptable answer if AerolVM is immature.
2. Add `Sandbox` protocol in `src/pablo/sandbox.py` with `LocalSandbox` (current behavior, used in
   tests and CI) and `ContainerSandbox`. `verify_diff` takes a sandbox; config picks one
   (`sandbox: container` in lanes.yaml).
3. Build image `pablo-check:py311` from `scripts/sandbox/Dockerfile` with pytest, mypy, ruff preinstalled.
   Project dependencies: install from the project's lockfile inside the container at build time of a
   per-project cached layer, not at check time with network.
4. Tests: a diff whose test opens a socket to 1.1.1.1 must fail in `ContainerSandbox`; a diff that
   writes outside the scratch dir must fail. Mark these tests `@pytest.mark.docker` and skip when
   Docker is unavailable (CI).

**Acceptance**
- Network and escape tests pass on Pablo; the default suite still passes in CI without Docker.
- `pablo run` on the sandbox repo uses the container. BUILD_PLAN M5 marked done.
