"""Proof beside claim: apply a diff to a scratch copy and run the checks.

Nothing touches your working tree until you approve. The scratch copy is a plain
directory today; Milestone 4 swaps it for an AerolVM sandbox with no network.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CHECKS: dict[str, list[str]] = {
    "tests": ["python", "-m", "pytest", "-q"],
    "types": ["python", "-m", "mypy", "."],
}
IGNORE = shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__", ".mypy_cache")


@dataclass
class Check:
    name: str
    command: list[str]
    passed: bool
    exit_code: int
    duration_s: float
    output_tail: str


@dataclass
class Verdict:
    applied: bool
    checks: list[Check] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    files: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.applied and all(c.passed for c in self.checks)

    def summary(self) -> str:
        if not self.applied:
            return f"diff did not apply: {self.error}"
        parts = [f"{c.name} {'passed' if c.passed else 'FAILED'}" for c in self.checks]
        return f"+{self.added} −{self.removed} · " + (", ".join(parts) or "no checks")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def diff_stats(diff: str) -> tuple[int, int, list[str]]:
    added = removed = 0
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != "/dev/null":
                files.append(path[2:] if path.startswith("b/") else path)
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed, files


def apply_diff(repo: Path, diff: str, *, check_only: bool = False) -> tuple[bool, str]:
    args = ["git", "apply", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    proc = subprocess.run(
        [*args, "-"], cwd=repo, input=diff, text=True, capture_output=True, check=False
    )
    return proc.returncode == 0, proc.stderr.strip()


def run_check(name: str, command: list[str], cwd: Path, timeout_s: float = 300) -> Check:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command, cwd=cwd, text=True, capture_output=True, timeout=timeout_s, check=False
        )
        code, out = proc.returncode, (proc.stdout + proc.stderr)
    except subprocess.TimeoutExpired:
        code, out = 124, f"timed out after {timeout_s:.0f}s"
    except FileNotFoundError as exc:
        code, out = 127, str(exc)
    return Check(
        name=name,
        command=command,
        passed=code == 0,
        exit_code=code,
        duration_s=round(time.perf_counter() - started, 2),
        output_tail="\n".join(out.strip().splitlines()[-30:]),
    )


def verify_diff(
    repo: Path, diff: str, checks: dict[str, list[str]] | None = None, timeout_s: float = 300
) -> Verdict:
    added, removed, files = diff_stats(diff)
    verdict = Verdict(applied=False, added=added, removed=removed, files=files)
    with tempfile.TemporaryDirectory(prefix="pablo-verify-") as tmp:
        scratch = Path(tmp) / "work"
        shutil.copytree(repo, scratch, ignore=IGNORE)
        ok, err = apply_diff(scratch, diff)
        if not ok:
            verdict.error = err or "git apply failed"
            return verdict
        verdict.applied = True
        for name, command in (checks if checks is not None else DEFAULT_CHECKS).items():
            verdict.checks.append(run_check(name, command, scratch, timeout_s))
    return verdict
