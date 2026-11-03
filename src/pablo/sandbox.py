"""Where checks run: a plain subprocess, or a locked-down container.

Checks run code the model wrote. `ContainerSandbox` gives that code no network, a
read-only image, a tmpfs /tmp, no capabilities, and CPU, memory, and process limits.
Only the scratch copy is mounted, at /work. It fails closed: if the container can't
start, the check fails. It never falls back to running on the host.

Project dependencies are baked into a per-project image (`pablo sandbox build`), keyed
by a hash of the repo's dependency files. Building is the only step with network, and
you run it yourself. A diff that changes dependencies is checked against the image
built from the current files, never one built from the diff.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pablo.config import SandboxConfig

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# Files that decide what a Python project installs. Their hash names the project image.
DEP_FILES = ("pyproject.toml", "uv.lock", "requirements.txt", "requirements-dev.txt")

ENGINE_FAILED = 125  # what podman and docker return when the container itself can't run

# The sentry and the guest share one address space. On runsc 20261026.0 a 4 GiB
# cap kills `uv`; 7 GiB (this 3 GiB plus a 4 GiB guest) lets 3 GiB through and
# kills a 4 GiB allocation. The wrapper reads PABLO_RUNSC_AS_KIB.
SENTRY_AS_OVERHEAD = 3 * 1024**3
_MEMORY = re.compile(r"^(\d+)([kmgt])?$", re.IGNORECASE)
_MEMORY_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}

PROJECT_CONTAINERFILE = """\
FROM {base}
COPY . /deps/
RUN set -eu; cd /deps; \\
    if [ -f uv.lock ]; then \\
      UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --inexact --no-install-project; \\
    elif [ -f requirements.txt ]; then \\
      uv pip install --python /opt/venv/bin/python -r requirements.txt; \\
    elif [ -f pyproject.toml ]; then \\
      uv pip install --python /opt/venv/bin/python -r pyproject.toml; \\
    fi; \\
    if [ -f requirements-dev.txt ]; then \\
      uv pip install --python /opt/venv/bin/python -r requirements-dev.txt; \\
    fi; \\
    rm -rf /deps
"""


@dataclass
class Result:
    exit_code: int
    output: str
    duration_s: float


class Sandbox(Protocol):
    @property
    def label(self) -> str: ...

    def ready(self) -> tuple[bool, str]: ...

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> Result: ...


class LocalSandbox:
    """Checks as plain subprocesses with your user's permissions. Tests, CI, and --fake."""

    label = "local"

    def __init__(self, runner: Runner = subprocess.run) -> None:
        self._runner = runner

    def ready(self) -> tuple[bool, str]:
        return True, ""

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> Result:
        started = time.perf_counter()
        try:
            proc = self._runner(
                list(command), cwd=cwd, text=True, capture_output=True, timeout=timeout_s
            )
            code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            code, out = 124, f"timed out after {timeout_s:.0f}s"
        except FileNotFoundError as exc:
            code, out = 127, str(exc)
        return Result(code, out, round(time.perf_counter() - started, 2))


class ContainerSandbox:
    """One throwaway container per check. Nothing but the scratch copy is visible."""

    def __init__(
        self,
        cfg: SandboxConfig,
        repo: Path | None = None,
        runner: Runner = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.cfg = cfg
        self.repo = repo
        self._runner = runner
        self._which = which
        self._ready = False
        self.image = project_image(cfg.image, repo) if repo and dep_files(repo) else cfg.image

    @property
    def label(self) -> str:
        return f"{self.cfg.engine}/{self.cfg.runtime or 'default'} {self.image}"

    def ready(self) -> tuple[bool, str]:
        """Engine on PATH and image built. Only success is cached: a build can fix it."""
        if self._ready:
            return True, ""
        if self._which(self.cfg.engine) is None:
            return False, f"{self.cfg.engine} not found"
        proc = self._runner(
            [self.cfg.engine, "image", "inspect", self.image],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            where = f" -C {self.repo}" if self.image != self.cfg.image else " --base"
            hint = "pablo sandbox build" + where
            return False, f"image {self.image} is not built (run: {hint})"
        self._ready = True
        return True, ""

    def argv(self, command: Sequence[str], scratch: Path, name: str) -> list[str]:
        cfg = self.cfg
        args = [
            cfg.engine,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,size={cfg.tmpfs_mb}m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(cfg.pids),
            # gVisor with --ignore-cgroups does not apply --pids-limit. RLIMIT_NPROC does.
            "--ulimit",
            f"nproc={cfg.pids}:{cfg.pids}",
            "--memory",
            cfg.memory,
            "--memory-swap",
            cfg.memory,
            "--cpus",
            str(cfg.cpus),
            "--volume",
            f"{scratch.resolve()}:/work:rw",
            "--workdir",
            "/work",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONPATH=/work/src:/work",
            # The baked venv. Checks must not sync: the image is read-only and has no network.
            "--env",
            "UV_PROJECT_ENVIRONMENT=/opt/venv",
            "--env",
            "UV_NO_SYNC=1",
            "--env",
            "UV_OFFLINE=1",
            "--env",
            "UV_CACHE_DIR=/tmp/uv-cache",
        ]
        if cfg.runtime:
            args += ["--runtime", cfg.runtime]
        args += self._user_args()
        return [*args, self.image, *command]

    def _user_args(self) -> list[str]:
        mode = self.cfg.user
        if mode == "root":
            return []  # rootless docker: container root is already you
        if mode == "auto" and Path(self.cfg.engine).name == "podman":
            return ["--userns=keep-id"]
        return ["--user", f"{os.getuid()}:{os.getgid()}"]

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> Result:
        ok, why = self.ready()
        if not ok:
            return Result(
                ENGINE_FAILED, f"sandbox unavailable: {why}. Checks never fall back to the host.", 0
            )
        name = f"pablo-check-{uuid.uuid4().hex[:12]}"
        argv = self.argv(command, cwd, name)
        # Affinity reaches the guest through conmon. --cpus does not, under --ignore-cgroups.
        if self.cfg.cpu_set:
            argv = ["taskset", "-c", self.cfg.cpu_set, *argv]
        # create is the invocation that spawns the sentry, and it inherits this.
        env = os.environ.copy()
        env["PABLO_RUNSC_AS_KIB"] = str(address_kib(self.cfg.memory))
        started = time.perf_counter()
        try:
            proc = self._runner(argv, text=True, capture_output=True, timeout=timeout_s, env=env)
            code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            # Killing the client doesn't stop the container, so remove it by name.
            self._runner(
                [self.cfg.engine, "rm", "--force", name], text=True, capture_output=True, timeout=60
            )
            code, out = 124, f"timed out after {timeout_s:.0f}s; container removed"
        except FileNotFoundError as exc:
            code, out = 127, str(exc)
        return Result(code, out, round(time.perf_counter() - started, 2))


def memory_bytes(spec: str) -> int:
    """Docker-style size (`4g`, `512m`) as bytes."""
    match = _MEMORY.fullmatch(spec.strip())
    if match is None:
        raise ValueError(f"sandbox memory must be an integer with optional k/m/g/t, got {spec!r}")
    unit = match.group(2).lower() if match.group(2) else ""
    return int(match.group(1)) * _MEMORY_UNITS[unit]


def address_kib(spec: str) -> int:
    """RLIMIT_AS for the sentry, in KiB: the configured guest cap plus its own share."""
    return (memory_bytes(spec) + SENTRY_AS_OVERHEAD) // 1024


def make_sandbox(cfg: SandboxConfig, repo: Path | None = None) -> Sandbox:
    if cfg.kind == "container":
        return ContainerSandbox(cfg, repo)
    return LocalSandbox()


def dep_files(repo: Path) -> list[Path]:
    return [repo / name for name in DEP_FILES if (repo / name).is_file()]


def deps_hash(repo: Path) -> str:
    digest = hashlib.sha256()
    for path in dep_files(repo):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def project_image(base: str, repo: Path) -> str:
    """`pablo-check-deps:<deps hash>`: named by the dependency files' contents, not the folder.

    Two copies of a project with the same pyproject/lock share one image, so an eval's
    throwaway copy of a repo uses the image you built for the real one.
    """
    return f"{base.split(':', 1)[0]}-deps:{deps_hash(repo)}"


def touches_deps(files: Sequence[str]) -> bool:
    return any(Path(f).name in DEP_FILES for f in files)


def build_base(cfg: SandboxConfig, dockerfile: Path, runner: Runner = subprocess.run) -> Any:
    """Build the base check image. Needs network; you run it, never a check."""
    context = str(dockerfile.parent)
    return runner(
        [cfg.engine, "build", "--tag", cfg.image, "--file", str(dockerfile), context], text=True
    )


def build_project(cfg: SandboxConfig, repo: Path, runner: Runner = subprocess.run) -> Any:
    """Bake the repo's dependencies into its own image on top of the base."""
    files = dep_files(repo)
    if not files:
        raise ValueError(f"{repo} has none of {', '.join(DEP_FILES)}; the base image is enough")
    tag = project_image(cfg.image, repo)
    with tempfile.TemporaryDirectory(prefix="pablo-sandbox-build-") as tmp:
        context = Path(tmp)
        for path in files:
            shutil.copy2(path, context / path.name)
        containerfile = context / "Containerfile"
        containerfile.write_text(PROJECT_CONTAINERFILE.format(base=cfg.image), encoding="utf-8")
        return runner(
            [cfg.engine, "build", "--tag", tag, "--file", str(containerfile), str(context)],
            text=True,
        )
