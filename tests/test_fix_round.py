"""Fix-on-red: when a change makes checks worse, the coder sees the failures once and can fix it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pablo.config import Settings
from pablo.gateway import FakeBackend
from pablo.orchestrator import Orchestrator

PYTEST = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})

BUGGY = """Added sub.

calc.py
<<<<<<< SEARCH
def add(a, b):
    return a + b
=======
def add(a, b):
    return a + b


def sub(a, b):
    return a + b
>>>>>>> REPLACE

test_calc.py
<<<<<<< SEARCH
=======
from calc import sub


def test_sub():
    assert sub(5, 3) == 2
>>>>>>> REPLACE
"""

# SEARCH copies the coder's own edited calc.py, which only exists after the first change.
FIX_ON_TOP = """sub returned a sum.

calc.py
<<<<<<< SEARCH
def sub(a, b):
    return a + b
=======
def sub(a, b):
    return a - b
>>>>>>> REPLACE
"""


def repo_with(tmp_path: Path) -> Path:
    import subprocess

    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_add.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


def test_the_coder_fixes_what_its_own_test_caught(settings: Settings, tmp_path: Path) -> None:
    seen: list[str] = []

    def coder(model: str, messages: list) -> str:  # type: ignore[type-arg]
        seen.append(messages[-1]["content"])
        return BUGGY if len(seen) == 1 else FIX_ON_TOP

    backend = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, msgs: JUDGE})
    run = Orchestrator(settings, backend, repo=repo_with(tmp_path), checks=PYTEST).start(
        "add sub(a, b) with a test", lane="coder"
    )
    assert run.fixed and run.state == "ready" and run.verdict and run.verdict["clean"]
    assert "+    return a - b" in run.diff and "+    return a + b" not in run.diff
    assert "passed before this diff" in seen[1] and "assert 8 == 2" in seen[1]


def test_a_fix_that_doesnt_help_keeps_the_first_try(settings: Settings, tmp_path: Path) -> None:
    replies = iter([BUGGY, "Hmm, let me think about it."])
    backend = FakeBackend(
        scripts={"pablo/coder": lambda m, msgs: next(replies), "pablo/judge": lambda m, msgs: JUDGE}
    )
    run = Orchestrator(settings, backend, repo=repo_with(tmp_path), checks=PYTEST).start(
        "add sub(a, b) with a test", lane="coder"
    )
    assert not run.fixed and run.state == "needs_you" and "return a + b" in run.diff
    assert run.certainty is not None and run.certainty <= 0.3  # still capped: checks got worse


def test_fix_rounds_zero_means_one_try(settings: Settings, tmp_path: Path) -> None:
    settings.lanes["coder"].fix_rounds = 0
    calls: list[int] = []

    def coder(model: str, messages: list) -> str:  # type: ignore[type-arg]
        calls.append(1)
        return BUGGY

    backend = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, msgs: JUDGE})
    Orchestrator(settings, backend, repo=repo_with(tmp_path), checks=PYTEST).start(
        "add sub(a, b) with a test", lane="coder"
    )
    assert len(calls) == 1
    assert settings.lane("claude").fix_rounds == 0  # remote lanes never get extra calls
