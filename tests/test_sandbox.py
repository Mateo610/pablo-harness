"""The sandbox without a container engine: argv, fail-closed, timeouts, image keys."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from pablo.config import SandboxConfig, Settings
from pablo.gateway import FakeBackend
from pablo.orchestrator import Orchestrator
from pablo.sandbox import (
    ContainerSandbox,
    LocalSandbox,
    address_kib,
    build_project,
    make_sandbox,
    project_image,
    touches_deps,
)
from pablo.verify import verify_diff

CFG = SandboxConfig(kind="container")


class FakeRunner:
    """Records every call. `inspect` decides whether the image exists."""

    def __init__(self, inspect: int = 0, run: int = 0, timeout: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.inspect, self.run_code, self.timeout = inspect, run, timeout

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        self.envs.append(kw.get("env") or {})
        cmd = argv[3:] if argv[:1] == ["taskset"] else argv
        if cmd[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, self.inspect, "", "")
        if cmd[1] == "run" and self.timeout:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))
        return subprocess.CompletedProcess(argv, self.run_code, "1 passed\n", "")


def box(runner: FakeRunner, found: str | None = "/usr/bin/podman", **cfg: Any) -> ContainerSandbox:
    conf = CFG.model_copy(update=cfg)
    return ContainerSandbox(conf, runner=runner, which=lambda _name: found)


def test_argv_locks_the_container_down(tmp_path: Path) -> None:
    argv = box(FakeRunner()).argv(["python", "-m", "pytest"], tmp_path, "pablo-check-x")
    joined = " ".join(argv)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--pids-limit 256",
        "--ulimit nproc=256:256",
        "--memory 4g --memory-swap 4g",
        "--runtime runsc",
        "--userns=keep-id",
        "--rm",
    ):
        assert flag in joined, flag
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--volume"]
    assert mounts == [f"{tmp_path.resolve()}:/work:rw"]  # the scratch copy and nothing else
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "--env"]
    assert envs == [
        "HOME=/tmp",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONPATH=/work/src:/work",
        "UV_PROJECT_ENVIRONMENT=/opt/venv",
        "UV_NO_SYNC=1",
        "UV_OFFLINE=1",
        "UV_CACHE_DIR=/tmp/uv-cache",
    ]
    assert argv[-4:] == ["pablo-check:py311", "python", "-m", "pytest"]


def test_docker_runs_as_you_and_root_mode_adds_nothing(tmp_path: Path) -> None:
    docker = box(FakeRunner(), found="/usr/bin/docker", engine="docker")
    assert "--user" in docker.argv(["true"], tmp_path, "n")
    rootless = box(FakeRunner(), user="root")
    argv = rootless.argv(["true"], tmp_path, "n")
    assert "--user" not in argv and "--userns=keep-id" not in argv


def test_fails_closed_without_engine_or_image(tmp_path: Path) -> None:
    runner = FakeRunner()
    result = box(runner, found=None).run(["python", "-m", "pytest"], tmp_path, 10)
    assert result.exit_code == 125 and "never fall back" in result.output
    assert runner.calls == []  # nothing ran, on the host or anywhere

    runner = FakeRunner(inspect=1)
    result = box(runner).run(["python", "-m", "pytest"], tmp_path, 10)
    assert result.exit_code == 125 and "sandbox build --base" in result.output
    assert all(call[1] != "run" for call in runner.calls)


def test_address_cap_is_the_guest_limit_plus_the_sentry() -> None:
    assert address_kib("4g") == (7 * 1024**3) // 1024
    assert address_kib("512m") == (512 * 1024**2 + 3 * 1024**3) // 1024


def test_run_pins_the_configured_cpus(tmp_path: Path) -> None:
    runner = FakeRunner()
    box(runner).run(["true"], tmp_path, 10)
    assert runner.calls[1][:4] == ["taskset", "-c", "12-15", "podman"]
    assert runner.envs[1]["PABLO_RUNSC_AS_KIB"] == str(address_kib("4g"))


def test_timeout_removes_the_container(tmp_path: Path) -> None:
    runner = FakeRunner(timeout=True)
    result = box(runner).run(["sleep", "99"], tmp_path, 1)
    assert result.exit_code == 124
    name = runner.calls[1][runner.calls[1].index("--name") + 1]
    assert runner.calls[-1] == ["podman", "rm", "--force", name]


def test_project_image_follows_dependency_files(tmp_path: Path) -> None:
    repo = tmp_path / "My Proj"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    first = project_image("pablo-check:py311", repo)
    assert first.startswith("pablo-check-my-proj:")
    assert project_image("pablo-check:py311", repo) == first
    (repo / "uv.lock").write_text("version = 1\n")
    assert project_image("pablo-check:py311", repo) != first
    # A repo with dependency files needs its own image; the hint names the repo.
    sandbox = ContainerSandbox(CFG, repo, runner=FakeRunner(inspect=1), which=lambda _n: "x")
    ok, why = sandbox.ready()
    assert not ok and "-C" in why and sandbox.image == project_image(CFG.image, repo)


def test_build_project_copies_only_dependency_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "secret.py").write_text("KEY = 1\n")
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        context = Path(argv[-1])
        seen["files"] = sorted(p.name for p in context.iterdir())
        seen["containerfile"] = (context / "Containerfile").read_text()
        seen["tag"] = argv[argv.index("--tag") + 1]
        return subprocess.CompletedProcess(argv, 0, "", "")

    build_project(CFG, tmp_path, runner)
    assert seen["files"] == ["Containerfile", "pyproject.toml"]
    assert seen["containerfile"].startswith("FROM pablo-check:py311")
    assert seen["tag"] == project_image(CFG.image, tmp_path)


def test_verify_records_sandbox_and_flags_dependency_changes(repo: Path) -> None:
    diff = (
        "diff --git a/requirements.txt b/requirements.txt\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/requirements.txt\n@@ -0,0 +1 @@\n+requests\n"
    )
    assert touches_deps(["requirements.txt"]) and not touches_deps(["calc.py"])
    runner = FakeRunner()
    verdict = verify_diff(repo, diff, {"tests": ["python", "-m", "pytest"]}, sandbox=box(runner))
    assert verdict.passed and verdict.sandbox.startswith("podman/runsc")
    assert verdict.notes and "rebuild" in verdict.notes[0]
    run_call = next(c for c in runner.calls if "run" in c[:5])
    assert run_call[-3:] == ["python", "-m", "pytest"]

    local = verify_diff(repo, diff, {"tests": [sys.executable, "-c", "pass"]})
    assert local.sandbox == "local" and local.notes == []


def test_orchestrator_uses_the_configured_sandbox(settings: Settings, repo: Path) -> None:
    assert isinstance(Orchestrator(settings, FakeBackend(), repo=repo).sandbox, LocalSandbox)
    settings.sandbox = SandboxConfig(kind="container")
    orch = Orchestrator(settings, FakeBackend(), repo=repo)
    assert isinstance(orch.sandbox, ContainerSandbox)
    assert isinstance(make_sandbox(SandboxConfig()), LocalSandbox)
