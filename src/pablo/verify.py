"""Proof beside claim: apply a diff to a scratch copy and run the checks.

Nothing touches your working tree until you approve. The diff is applied on the host
(git apply refuses paths outside the copy); the checks run in a `Sandbox`, which on
Pablo is a container with no network (see sandbox.py).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pablo.sandbox import LocalSandbox, Sandbox, touches_deps

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
    sandbox: str = "local"
    notes: list[str] = field(default_factory=list)

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
    ok, err = _git_apply(repo, diff, check_only=check_only)
    if ok:
        return True, ""
    # Models often invent one blank context line after the last real line.
    alt = _drop_phantom_blank_context(diff)
    if alt != diff:
        ok, err2 = _git_apply(repo, alt, check_only=check_only)
        if ok:
            return True, ""
        err = err2 or err
    return False, err


def _git_apply(repo: Path, diff: str, *, check_only: bool) -> tuple[bool, str]:
    args = ["git", "apply", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    proc = subprocess.run(
        [*args, "-"], cwd=repo, input=diff, text=True, capture_output=True, check=False
    )
    return proc.returncode == 0, proc.stderr.strip()


def _recount_hunks(lines: list[str]) -> list[str]:
    """Rewrite @@ counts from the lines that follow. Models undercount by one."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            out.append(line)
            i += 1
            continue
        hunk: list[str] = []
        i += 1
        while i < len(lines) and not lines[i].startswith(("@@", "diff --git", "--- ")):
            hunk.append(lines[i])
            i += 1
        old = new = 0
        for hline in hunk:
            if hline.startswith("+"):
                new += 1
            elif hline.startswith("-"):
                old += 1
            elif hline.startswith("\\"):
                continue
            else:
                old += 1
                new += 1
        header = line
        start = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
        if start and (old or new):
            suffix = start.group(3)
            header = f"@@ -{start.group(1)},{old} +{start.group(2)},{new} @@{suffix}"
        out.append(header)
        out.extend(hunk)
    return out


def _drop_phantom_blank_context(diff: str) -> str:
    """Remove a blank context line that sits immediately before the first change."""
    lines = diff.splitlines()
    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            out.append(line)
            i += 1
            continue
        header = line
        i += 1
        hunk: list[str] = []
        while i < len(lines) and not lines[i].startswith(("@@", "diff --git", "--- ")):
            hunk.append(lines[i])
            i += 1
        change = next((n for n, hline in enumerate(hunk) if hline[:1] in "+-"), None)
        if change is not None:
            start = change
            while start > 0 and hunk[start - 1] == " ":
                start -= 1
            if start != change:
                hunk = hunk[:start] + hunk[change:]
                changed = True
        out.append(header)
        out.extend(hunk)
    if not changed:
        return diff if diff.endswith("\n") or not diff else diff + "\n"
    text = "\n".join(_recount_hunks(out))
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def run_check(
    name: str,
    command: list[str],
    cwd: Path,
    timeout_s: float = 300,
    sandbox: Sandbox | None = None,
) -> Check:
    result = (sandbox or LocalSandbox()).run(command, cwd, timeout_s)
    return Check(
        name=name,
        command=command,
        passed=result.exit_code == 0,
        exit_code=result.exit_code,
        duration_s=result.duration_s,
        output_tail="\n".join(result.output.strip().splitlines()[-30:]),
    )


def verify_diff(
    repo: Path,
    diff: str,
    checks: dict[str, list[str]] | None = None,
    timeout_s: float = 300,
    sandbox: Sandbox | None = None,
) -> Verdict:
    box = sandbox or LocalSandbox()
    added, removed, files = diff_stats(diff)
    verdict = Verdict(applied=False, added=added, removed=removed, files=files, sandbox=box.label)
    if touches_deps(files) and box.label != "local":
        verdict.notes.append(
            "This diff changes dependencies. Checks ran against the image built from the "
            "current files; rebuild it (pablo sandbox build) after approving."
        )
    with tempfile.TemporaryDirectory(prefix="pablo-verify-") as tmp:
        scratch = Path(tmp) / "work"
        shutil.copytree(repo, scratch, ignore=IGNORE)
        ok, err = apply_diff(scratch, diff)
        if not ok:
            verdict.error = err or "git apply failed"
            return verdict
        verdict.applied = True
        for name, command in (checks if checks is not None else DEFAULT_CHECKS).items():
            verdict.checks.append(run_check(name, command, scratch, timeout_s, box))
    return verdict
