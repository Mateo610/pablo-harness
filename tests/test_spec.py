"""Task-derived checks: written from the request before the diff is seen, run before and after."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tests.fakes import FakeBackend

from pablo.config import Settings
from pablo.evals import SuiteReport, TaskResult
from pablo.gateway import Message
from pablo.orchestrator import Orchestrator
from pablo.spec import SPEC_PROMPT, classify, parse_pytest, pytest_command, spec_source

PY = [sys.executable, "-m", "pytest", "-q"]
CHECKS = {"tests": PY}
TASK = "Add sub(a, b) to calc.py. sub(5, 3) must return 2."


def blocks(body: str) -> str:
    return (
        "Done.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
        f"def add(a, b):\n    return a + b\n\n\n{body}>>>>>>> REPLACE\n"
    )


RIGHT = blocks("def sub(a, b):\n    return a - b\n")
WRONG = blocks("def sub(a, b):\n    return a + b\n")  # its own test would agree with it
SPEC = (
    "```python\nfrom calc import sub\n\n\ndef test_sub():\n"
    '    assert sub(5, 3) == 2, "task: sub(5, 3) must return 2"\n```'
)
GUESSED_WRONG = (
    "```python\nfrom calc import subtract\n\n\ndef test_sub():\n    assert subtract(5, 3) == 2\n```"
)
JUDGE_OK = json.dumps({"certainty": 0.95, "summary": "adds sub", "question": ""})


def orch(  # type: ignore[no-untyped-def]
    settings: Settings, repo: Path, coder: str, spec: str, enabled: bool = True
):
    seen: list[str] = []

    def judge(model: str, msgs: Sequence[Message]) -> str:
        if msgs[0]["content"] == SPEC_PROMPT:
            seen.append(msgs[-1]["content"])
            return spec
        return JUDGE_OK

    settings.spec.enabled = enabled
    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: coder, "pablo/judge": judge})
    return Orchestrator(settings, backend, repo=repo, checks=CHECKS), seen


def test_a_check_from_the_request_catches_what_the_judge_missed(
    settings: Settings, repo: Path
) -> None:
    o, seen = orch(settings, repo, WRONG, SPEC)
    run = o.start(TASK, lane="coder")
    assert run.verdict and run.verdict["clean"]  # the repo's own checks are happy
    assert run.spec and run.spec["status"] == "failed"
    assert run.state == "needs_you" and run.certainty is not None and run.certainty <= 0.5
    assert "sub(5, 3) must return 2" in run.question
    # The writer never saw the diff: no new function, no edit block.
    assert "def sub" not in seen[0] and "REPLACE" not in seen[0]


def test_a_right_change_is_confirmed(settings: Settings, repo: Path) -> None:
    o, _ = orch(settings, repo, RIGHT, SPEC)
    run = o.start(TASK, lane="coder")
    assert run.spec and run.spec["status"] == "confirmed" and run.state == "ready"


def test_a_badly_guessed_check_never_blocks_good_work(settings: Settings, repo: Path) -> None:
    o, _ = orch(settings, repo, RIGHT, GUESSED_WRONG)
    run = o.start(TASK, lane="coder")
    assert run.spec and run.spec["status"] == "broken" and run.state == "ready"
    o, _ = orch(settings, repo, RIGHT, "NONE")
    run = o.start(TASK, lane="coder")
    assert run.spec and run.spec["status"] == "skipped" and run.state == "ready"


def test_off_by_default_means_no_extra_call(settings: Settings, repo: Path) -> None:
    o, seen = orch(settings, repo, WRONG, SPEC, enabled=False)
    run = o.start(TASK, lane="coder")
    assert run.spec is None and seen == [] and run.state == "ready"  # the known blind spot


def test_reading_pytest_output() -> None:
    failed = parse_pytest(
        "F\nE       AssertionError: task: sub(5, 3) must return 2\nE       assert 8 == 2\n"
        "1 failed in 0.02s"
    )
    assert failed.failed == 1 and failed.asserts[0].startswith("AssertionError")
    import_error = parse_pytest("E   ImportError: cannot import name 'subtract'\n1 error in 0.1s")
    assert import_error.errors == 1 and import_error.other and not import_error.asserts
    attr = parse_pytest(
        "E       AttributeError: module 'calc' has no attribute 'x'\n1 failed in 0.1s"
    )
    assert classify(attr, parse_pytest("1 passed in 0.1s"))[0] == "broken"
    ok = parse_pytest("2 passed in 0.05s")
    assert classify(ok, parse_pytest("2 passed in 0.05s"))[0] == "uninformative"
    assert classify(ok, failed)[0] == "confirmed"
    assert spec_source("NONE") is None and spec_source("```python\nx = 1\n```") is None
    command = pytest_command(
        {"lint": ["ruff", "check", "."], "tests": ["uv", "run", "pytest", "-q", "tests"]}
    )
    assert command and command[:4] == ["uv", "run", "pytest", "-q"] and "--noconftest" in command
    assert command[-1] == "test_pablo_spec.py" and "tests" not in command


def test_the_suite_reports_catches_and_false_alarms() -> None:
    def row(i: str, spec: str, passed: bool) -> TaskResult:
        return TaskResult(i, "file", "fix", "needs_you", True, True, passed, 0.5, 1.0, spec=spec)

    report = SuiteReport("coder", "pablo/coder", [
        row("a", "failed", False), row("b", "failed", True), row("c", "confirmed", True)
    ])  # fmt: skip
    s = report.summary()
    assert s["spec_caught"] == ["a"] and s["spec_false_alarm"] == ["b"]
    assert s["spec"] == {"confirmed": 1, "failed": 2} and "caught ['a']" in report.to_markdown()
