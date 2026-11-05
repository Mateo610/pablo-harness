"""Task-derived checks: tests written from what you asked for, by a model that never saw the diff.

The suite's worst misses (`reading-time`, `memory-units`) had the coder write a test that agreed
with its own wrong answer: checks passed, every judge said ready. A spec check is written from the
task's concrete facts only ("200 words reads as 1 minute", "GiB is accepted") by the judge model,
before it sees the change, then run in the sandbox on the unedited code and on the changed code:

    original    changed     status
    fails       passes      confirmed      the change does what was asked
    any         fails       failed         an assertion from your words fails: Needs you
    passes      passes      uninformative  the check didn't touch the change
    -           errors      broken         the check guessed the code wrong: ignored

Only assertion failures count as "failed"; an ImportError or a wrong argument count means the
check is bad, not the change, so it can't block good work. Python repos with a pytest check only.
Off by default until the suite says it helps (`spec.enabled`).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pablo.gateway import ChatBackend, Message
from pablo.sandbox import LocalSandbox, Sandbox
from pablo.verify import IGNORE, apply_diff

Status = Literal["confirmed", "failed", "uninformative", "broken", "skipped"]
SPEC_FILE = "test_pablo_spec.py"
SPEC_CAP = 0.5

SPEC_PROMPT = """You write acceptance tests from a task description, before anyone changes the code.

Write pytest tests for ONLY the concrete, checkable facts the task states: example inputs and
outputs, numbers, units, names, error cases, things it says must not change. Use the code's
public functions and modules as the task and the repo show them (a new function the task names
will exist after the change). Import exactly as the repo's own tests do.

- At most 5 short tests, one fact each, with a plain assert and a message quoting the task.
- No mocks, no network, no files outside a tmp_path, no new dependencies.
- Don't test anything the task doesn't state. Don't guess behavior.
- If the task states nothing concrete enough to test, reply exactly: NONE

Reply with one ```python block holding the whole test file."""

_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)
_E_LINE = re.compile(r"^E\s+(.*)$", re.M)
_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors)\b")
_NOT_THE_CHANGE = re.compile(
    r"\b(ImportError|ModuleNotFoundError|AttributeError|NameError|SyntaxError|IndentationError|"
    r"FixtureLookupError|TypeError: .*(argument|positional|keyword))"
)


@dataclass
class SpecRun:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    asserts: list[str] = field(default_factory=list)  # assertion messages from failing tests
    other: list[str] = field(default_factory=list)  # non-assertion failure lines
    output_tail: str = ""


@dataclass
class SpecResult:
    status: Status
    reason: str = ""
    source: str = ""
    changed: SpecRun | None = None
    original: SpecRun | None = None

    @property
    def failing(self) -> list[str]:
        return self.changed.asserts if self.changed else []

    def evidence(self) -> str:
        if self.status == "confirmed":
            n = self.changed.passed if self.changed else 0
            return f"Task-derived checks: {n} pass on the change and failed before it."
        if self.status == "failed":
            return "Task-derived checks FAILED on the change: " + "; ".join(self.failing[:3])
        return f"Task-derived checks: {self.status} ({self.reason})." if self.reason else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def spec_source(reply: str) -> str | None:
    """The test file from the writer's reply, or None for NONE / no code."""
    if reply.strip().upper().startswith("NONE"):
        return None
    match = _BLOCK.search(reply)
    code = (match.group(1) if match else "").strip()
    return code + "\n" if "def test" in code else None


def write_spec(backend: ChatBackend, model: str, task: str, context: str) -> str | None:
    messages: list[Message] = [
        {"role": "system", "content": SPEC_PROMPT},
        {"role": "user", "content": f"Task:\n{task}\n\nThe repo, before the change:\n{context}"},
    ]
    return spec_source(backend.complete(model, messages, max_tokens=900, temperature=0.0).text)


def parse_pytest(output: str) -> SpecRun:
    tail = "\n".join(output.strip().splitlines()[-40:])
    run = SpecRun(output_tail=tail)
    for num, word in _COUNTS.findall(output.splitlines()[-1] if output.strip() else ""):
        if word == "passed":
            run.passed = int(num)
        elif word == "failed":
            run.failed = int(num)
        else:
            run.errors = int(num)
    for line in _E_LINE.findall(output):
        (run.other if _NOT_THE_CHANGE.search(line) else run.asserts).append(line.strip())
    return run


def pytest_command(checks: dict[str, list[str]]) -> list[str] | None:
    """The repo's own pytest invocation, so the spec runs with the same environment."""
    for command in checks.values():
        if any("pytest" in part for part in command):
            keep = [p for p in command if not p.startswith(("tests", "test/")) and p != "."]
            # --noconftest: a conftest the diff adds (e.g. one that skips tests) cannot touch it.
            flags = ["-p", "no:cacheprovider", "--noconftest", "--tb=short", "-o", "addopts="]
            return [*keep, *flags, SPEC_FILE]
    return None


def _run(
    repo: Path, diff: str | None, source: str, command: list[str], box: Sandbox, timeout_s: float
) -> SpecRun | None:
    with tempfile.TemporaryDirectory(prefix="pablo-spec-") as tmp:
        scratch = Path(tmp) / "work"
        shutil.copytree(repo, scratch, ignore=IGNORE)
        if diff is not None and not apply_diff(scratch, diff)[0]:
            return None
        (scratch / SPEC_FILE).write_text(source, encoding="utf-8")
        return parse_pytest(box.run(command, scratch, timeout_s).output)


def classify(changed: SpecRun, original: SpecRun) -> tuple[Status, str]:
    if changed.errors or (changed.failed and not changed.asserts):
        return "broken", "the check itself didn't run cleanly"
    if changed.failed:
        return "failed", f"{changed.failed} of {changed.failed + changed.passed} failed"
    if not changed.passed:
        return "broken", "no tests ran"
    if original.failed or original.errors:
        return "confirmed", ""
    return "uninformative", "they pass with or without the change"


def run_spec(
    repo: Path,
    diff: str,
    source: str,
    checks: dict[str, list[str]],
    sandbox: Sandbox | None = None,
    timeout_s: float = 120,
) -> SpecResult:
    command = pytest_command(checks)
    if command is None:
        return SpecResult("skipped", "no pytest check in this repo", source)
    box = sandbox or LocalSandbox()
    changed = _run(repo, diff, source, command, box, timeout_s)
    if changed is None:
        return SpecResult("skipped", "the diff didn't apply", source)
    original = _run(repo, None, source, command, box, timeout_s) or SpecRun()
    status, reason = classify(changed, original)
    return SpecResult(status, reason, source, changed, original)
