"""Learning without retraining: approvals become examples, misses notes, outcomes routing."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pablo.config import Settings
from pablo.gateway import FakeBackend, Message
from pablo.lessons import Example, Failure, Lessons, Outcome
from pablo.orchestrator import Orchestrator

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})


def blocks(body: str) -> str:
    return (
        "Done.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
        f"def add(a, b):\n    return a + b\n\n\n{body}>>>>>>> REPLACE\n"
    )


GOOD = blocks("def sub(a, b):\n    return a - b\n")
BREAKS = (
    "Done.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
    "def add(a, b):\n    return a * b\n>>>>>>> REPLACE\n"
)


def orch(settings: Settings, repo: Path, reply: str = GOOD) -> tuple[Orchestrator, list[str]]:
    prompts: list[str] = []

    def coder(model: str, msgs: Sequence[Message]) -> str:
        prompts.append(msgs[1]["content"])
        return reply

    scripts = {"pablo/coder": coder, "pablo/deep": coder, "pablo/judge": lambda m, x: JUDGE}
    return Orchestrator(settings, FakeBackend(scripts=scripts), repo=repo, checks=CHECKS), prompts


def test_your_decisions_become_lessons(settings: Settings, repo: Path) -> None:
    o, _ = orch(settings, repo)
    kept = o.start("add sub(a, b) to calc", lane="coder")
    o.approve(kept.id)
    dropped = o.start("add sub(a, b) to calc again", lane="coder")
    o.reject(dropped.id)
    lessons = Lessons(settings.home)
    assert [e.source for e in lessons.examples()] == ["approved"]
    assert lessons.examples()[0].diff == kept.diff
    record = lessons.lane_record(repo.name, lessons.examples()[0].kind)
    assert record["coder"] == (1, 2)


def test_examples_reach_the_coder_only_when_turned_on(settings: Settings, repo: Path) -> None:
    Lessons(settings.home).add_example(
        Example(
            repo.name,
            "add mul(a, b) to calc",
            "+def mul(a, b):\n+    return a * b\n",
            "coder",
            "approved",
        )
    )
    o, prompts = orch(settings, repo)
    o.start("add sub(a, b) to calc", lane="coder")
    assert "right in this repo before" not in prompts[-1]
    settings.learn.examples = True
    o, prompts = orch(settings, repo)
    o.start("add sub(a, b) to calc", lane="coder")
    assert "right in this repo before" in prompts[-1] and "def mul" in prompts[-1]


def test_a_check_that_got_worse_becomes_a_note(settings: Settings, repo: Path) -> None:
    o, _ = orch(settings, repo, BREAKS)
    o.settings.lanes["coder"].fix_rounds = 0
    o.start("make add faster", lane="coder")
    notes = Lessons(settings.home).failures()
    assert notes and notes[0].lane == "coder" and notes[0].error
    settings.learn.failures = True
    o, prompts = orch(settings, repo)
    o.start("make add even faster", lane="coder")
    assert "broke with" in prompts[-1]


def test_routing_learns_from_outcomes(settings: Settings, repo: Path) -> None:
    from pablo.tasks import detect_task_type

    kind = detect_task_type("add sub(a, b) in calc")
    lessons = Lessons(settings.home)
    for _ in range(5):
        lessons.add_outcome(Outcome(repo.name, kind, "coder", False))
        lessons.add_outcome(Outcome(repo.name, kind, "deep", True))
    settings.learn.routing = True
    o, _ = orch(settings, repo)
    run = o.begin("add sub(a, b) in calc")
    assert run.lane == "deep" and run.route_reason.startswith("learned: deep got 5/5")
    assert o.begin("add sub(a, b) in calc", lane="coder").lane == "coder"
    settings.learn.routing = False
    assert orch(settings, repo)[0].begin("add sub(a, b) in calc").lane != "deep"


def test_claude_and_partial_approvals_teach_nothing(settings: Settings, repo: Path) -> None:
    o, _ = orch(settings, repo)
    run = o.start("add sub(a, b) to calc", lane="coder")
    run.lane = "claude"
    o._learn(run, ok=True, whole=True)
    run.lane = "coder"
    o._learn(run, ok=True, whole=False)
    assert Lessons(settings.home).examples() == []


def test_frozen_lessons_are_read_only(tmp_path: Path) -> None:
    live = Lessons(tmp_path)
    live.add_failure(Failure("r", "t", "coder", "boom"))
    frozen = Lessons(tmp_path, frozen=True)
    frozen.add_failure(Failure("r", "t2", "coder", "bang"))
    assert [f.error for f in frozen.failures()] == ["boom"]


def test_nightly_practice_runs_once_a_night(settings: Settings, repo: Path, tmp_path: Path) -> None:
    from pablo.overnight import BriefStore, JobQueue, OvernightRunner, Scheduler

    project = tmp_path / "practice-me"
    shutil.copytree(repo, project)
    settings.practice.repos = [str(project)]
    settings.practice.nightly_tasks = 1
    o, _ = orch(settings, repo)
    runner = OvernightRunner(o, JobQueue(settings.home), settings.overnight)
    sched = Scheduler(runner, BriefStore(settings.home), repo)
    seen: list[str] = []
    import pablo.practice as practice

    original = practice.Practice.session
    practice.Practice.session = lambda self, r, n=10, kinds=("reimplement", "bugfix"), **kw: (
        seen.append(str(r)) or []
    )  # type: ignore[method-assign,assignment]
    try:
        night = datetime(2026, 11, 6, 2, 0)
        sched.practice(night)
        sched.practice(night)
        assert seen == [str(project)]
    finally:
        practice.Practice.session = original  # type: ignore[method-assign]


def test_an_eval_can_measure_examples_without_changing_them(
    settings: Settings, tmp_path: Path
) -> None:
    from pablo.evals import run_suite
    from pablo.sandbox import LocalSandbox

    home = tmp_path / "practiced"
    line = "+    return Decimal(raw)\n"
    Lessons(home).add_example(Example("ledger", "fix parse of amounts", line, "coder", "practice"))
    prompts: list[str] = []

    def coder(model: str, msgs: Sequence[Message]) -> str:
        prompts.append(msgs[1]["content"])
        return "I'm not sure what to change."

    backend = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    run_suite(settings, backend, only={"ledger-by-payee-sums"}, python=sys.executable,
              sandbox=LocalSandbox(), learn={"examples"}, lessons=home / "lessons")  # fmt: skip
    assert "right in this repo before" in prompts[0] and "Decimal(raw)" in prompts[0]
    assert len(Lessons(home).examples()) == 1 and not Lessons(home).failures()
