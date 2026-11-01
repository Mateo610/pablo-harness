"""Before-and-after checks: a diff is blamed only for what it made worse."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pablo.config import Settings
from pablo.gateway import FakeBackend
from pablo.orchestrator import Orchestrator
from pablo.sandbox import LocalSandbox, Result
from pablo.verify import Baselines, count_failures, verify_diff

PYTEST = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}

GOOD = "def test_add():\n    assert add(2, 3) == 5\n"
BROKEN = "def test_already_broken():\n    assert add(1, 1) == 3\n"


def project(tmp_path: Path, *tests: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    body = "from calc import add\n\n\n" + "\n\n".join(tests)
    (root / "test_calc.py").write_text(body)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


def diff_of(repo: Path, rel: str, text: str) -> str:
    original = (repo / rel).read_text() if (repo / rel).exists() else None
    (repo / rel).write_text(text)
    subprocess.run(["git", "add", "-N", rel], cwd=repo, check=True)
    diff = subprocess.run(
        ["git", "diff"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout
    if original is None:
        subprocess.run(["git", "rm", "-q", "--cached", rel], cwd=repo, check=True)
        (repo / rel).unlink()
    else:
        (repo / rel).write_text(original)
    return diff


class Counting(LocalSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.runs = 0

    def run(self, command: Sequence[str], cwd: Path, timeout_s: float) -> Result:
        self.runs += 1
        return super().run(command, cwd, timeout_s)


def test_count_failures() -> None:
    assert count_failures("1 failed, 2 passed, 1 error in 0.51s") == 2
    assert count_failures("Found 3 errors in 2 files (checked 4 source files)") == 3
    assert count_failures("FAILED test_x.py::test_y - assert 1 == 2") is None


def test_breaking_a_passing_check_is_a_regression(tmp_path: Path) -> None:
    repo = project(tmp_path, GOOD)
    diff = diff_of(repo, "calc.py", "def add(a, b):\n    return a - b\n")
    verdict = verify_diff(repo, diff, PYTEST)
    assert verdict.checks[0].status == "regressed"
    assert not verdict.clean and "passed before this diff" in verdict.summary()


def test_an_already_failing_check_is_not_the_diffs_fault(tmp_path: Path) -> None:
    repo = project(tmp_path, GOOD, BROKEN)
    diff = diff_of(repo, "calc.py", 'def add(a, b):\n    """Sum."""\n    return a + b\n')
    verdict = verify_diff(repo, diff, PYTEST)
    check = verdict.checks[0]
    assert not verdict.passed and verdict.clean
    assert check.status == "already failing" and check.before_failures == 1 == check.failures
    assert "already failing before this diff: 1 → 1" in verdict.summary()
    assert verdict.to_dict()["checks"][0]["status"] == "already failing"


def test_more_failures_than_before_is_not_clean(tmp_path: Path) -> None:
    repo = project(tmp_path, GOOD, BROKEN)
    diff = diff_of(repo, "calc.py", "def add(a, b):\n    return a * b\n")
    verdict = verify_diff(repo, diff, PYTEST)
    assert verdict.checks[0].status == "still failing" and not verdict.clean


def test_the_baseline_runs_once_per_repo_state(tmp_path: Path) -> None:
    repo = project(tmp_path, GOOD, BROKEN)
    box, seen = Counting(), Baselines()
    diff = diff_of(repo, "calc.py", 'def add(a, b):\n    """Sum."""\n    return a + b\n')
    verify_diff(repo, diff, PYTEST, sandbox=box, baselines=seen)
    assert box.runs == 2  # the diff, then the unedited copy
    verify_diff(repo, diff, PYTEST, sandbox=box, baselines=seen)
    assert box.runs == 3  # the baseline came from the cache
    (repo / "notes.md").write_text("the repo moved on\n")
    verify_diff(repo, diff, PYTEST, sandbox=box, baselines=seen)
    assert box.runs == 5  # the repo changed, so the baseline ran again


def test_a_good_diff_on_a_red_repo_can_be_ready(settings: Settings, tmp_path: Path) -> None:
    repo = project(tmp_path, GOOD, BROKEN)
    block = (
        "Docstring.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n=======\n"
        'def add(a, b):\n    """Sum."""\n>>>>>>> REPLACE\n'
    )
    backend = FakeBackend(
        scripts={
            "pablo/coder": [block],
            "pablo/judge": [json.dumps({"certainty": 0.9, "summary": "fine", "question": ""})],
        }
    )
    run = Orchestrator(settings, backend, repo=repo, checks=PYTEST).start(
        "add a docstring to add", lane="coder"
    )
    assert run.verdict is not None and run.verdict["clean"] and not run.verdict["passed"]
    assert run.certainty == 0.9 and run.state == "ready"
    judge_prompt = backend.calls[-1][1][1]["content"]
    assert "already failing before this diff" in judge_prompt
