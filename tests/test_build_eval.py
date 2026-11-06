"""The build set checks itself, and `pablo eval-build` runs plan mode end to end on fakes."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from pablo.build_eval import acceptance_check, build_markdown, build_summary, load_build, run_build
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT
from pablo.gateway import FakeBackend, Message
from pablo.sandbox import LocalSandbox

TASKS = load_build()
PY = sys.executable
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})


def test_build_set_shape() -> None:
    assert len(TASKS) >= 5 and len({t.id for t in TASKS}) == len(TASKS)
    for t in TASKS:
        assert "def test_" in t.acceptance and t.solution


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_acceptance_needs_the_tool(tmp_path: Path, task) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    assert not acceptance_check(task, repo, LocalSandbox(), python=PY)[0]
    ok, out = acceptance_check(task, repo, LocalSandbox(), files=task.solution, python=PY)
    assert ok, out


def fake_builder(task_id: str, *, plans: bool = True, says_build: bool = True) -> FakeBackend:
    """A chat model that plans two steps and a coder that writes the reference, file by file."""
    task = next(t for t in TASKS if t.id == task_id)
    files = list(task.solution.items())

    def coder(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"]
        if CHAT_PROMPT.splitlines()[0] in msgs[0]["content"]:
            if "go" in last.lower() and "plan it first" not in last.lower():
                return "Building.\nBUILD: go" if says_build else "Sure, starting."
            if not plans:
                return "RUN: build it all"
            steps = "\n".join(f"STEP: write {rel}" for rel, _ in files)
            return f"Here's the plan.\nPLAN: {task.title}\n{steps}"
        wanted = re.search(r"write (\S+)", last)
        rel = wanted.group(1) if wanted else files[0][0]
        text = dict(files).get(rel, files[0][1])
        return f"Done.\n\n{rel}\n<<<<<<< SEARCH\n=======\n{text}>>>>>>> REPLACE\n"

    return FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})


def test_a_planned_tool_is_built_step_by_step_and_accepted(settings: Settings) -> None:
    task = "build-units"  # two files: the steps must build on each other
    results = run_build(settings, fake_builder(task), only={task}, sandbox=LocalSandbox(),
                        python=PY)  # fmt: skip
    r = results[0]
    assert r.error == "" and r.planned and not r.asked_twice and not r.pressed_build
    assert r.steps == 2 and r.approved == 2 and r.rejected == 0 and r.finished
    assert r.accepted, r.acceptance_tail
    summary = build_summary(results, lane="auto", commit="abc")
    assert summary["accepted"] == 1 and "| build-units | yes | 2 |" in build_markdown(summary)


def test_what_it_records_when_the_model_doesnt_plan_or_doesnt_start(settings: Settings) -> None:
    no_plan = run_build(settings, fake_builder("build-todo", plans=False), only={"build-todo"},
                        sandbox=LocalSandbox(), python=PY)[0]  # fmt: skip
    assert no_plan.asked_twice and not no_plan.planned and not no_plan.accepted
    lazy = run_build(settings, fake_builder("build-todo", says_build=False), only={"build-todo"},
                     sandbox=LocalSandbox(), python=PY)[0]  # fmt: skip
    assert lazy.pressed_build and lazy.finished and lazy.accepted
