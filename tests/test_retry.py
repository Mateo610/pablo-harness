"""Coder → deep: a run that needs you gets a fresh try on the lane's `retry_on` lane."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pablo.config import Settings
from pablo.edits import EditBlock, EditError, apply_block
from pablo.gateway import FakeBackend
from pablo.orchestrator import Orchestrator, unseen_files
from pablo.overnight import JobQueue

PYTEST = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
GOOD = (
    "Added sub.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
    "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n>>>>>>> REPLACE\n"
)


def backend(coder_reply: str) -> FakeBackend:
    return FakeBackend(
        scripts={
            "pablo/coder": lambda m, msgs: coder_reply,
            "pablo/deep": lambda m, msgs: GOOD,
            "pablo/judge": lambda m, msgs: JUDGE,
        }
    )


def orch(settings: Settings, repo: Path, reply: str, when: str = "now") -> Orchestrator:
    settings.lanes["coder"].retry_when = when  # type: ignore[assignment]
    o = Orchestrator(settings, backend(reply), repo=repo, checks=PYTEST)
    o.retry_inline = True
    return o


def test_a_coder_run_that_needs_you_is_retried_on_deep(settings: Settings, repo: Path) -> None:
    o = orch(settings, repo, "I'm not sure what to change.")
    run = o.start("add sub(a, b)", lane="coder")
    assert run.state == "needs_you" and run.retry_run
    retry = o.get(run.retry_run)
    assert retry.lane == "deep" and retry.retried_from == run.id and retry.state == "ready"
    assert retry.group == run.group and "ready for you" in run.question
    o.approve(retry.id)  # approving the retry rejects the coder's run (same group)
    assert o.get(run.id).state == "rejected"


def test_ready_runs_compares_and_retries_never_retry(settings: Settings, repo: Path) -> None:
    o = orch(settings, repo, GOOD)
    assert o.start("add sub(a, b)", lane="coder").retry_run is None  # it was ready
    o = orch(settings, repo, "no idea")
    a, b = o.compare("add sub", ["coder", "deep"])
    assert a.retry_run is None and b.retry_run is None
    settings.lanes["coder"].retry_when = "off"
    assert o.start("add sub(a, b)", lane="coder").retry_run is None


def test_overnight_queues_the_retry_with_its_link(settings: Settings, repo: Path) -> None:
    o = orch(settings, repo, "no idea", when="overnight")
    run = o.start("add sub(a, b)", lane="coder")
    job = next(j for j in JobQueue(settings.home).pending() if j.id == run.retry_job)
    assert job.lane == "deep" and job.retried_from == run.id and job.group == run.group
    assert "tonight" in run.question


def test_retry_never_goes_to_claude(settings: Settings) -> None:
    data = settings.model_dump()
    data["lanes"]["coder"]["retry_on"] = "claude"
    with pytest.raises(ValueError, match="retry_on"):
        Settings.model_validate(data)


def test_a_miss_says_where_search_went_wrong() -> None:
    content = "def clip(text, cap):\n    if len(text) <= cap:\n        return text\n    return ''\n"
    block = EditBlock(
        "ctx.py", "def clip(text, cap):\n    if not text:\n        return text\n", "x"
    )
    with pytest.raises(EditError, match=r"matches 1 line\(s\) from line 1.*'if len\(text\)"):
        apply_block(content, block)


def test_the_repair_shows_files_the_coder_was_never_shown(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "context.py").write_text("def clip(text, cap):\n    return text\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    reply = (
        "pkg/context.py\n<<<<<<< SEARCH\ndef clip(t):\n=======\ndef clip(t, c):\n>>>>>>> REPLACE\n"
    )
    extra = unseen_files(tmp_path, reply, shown=["pkg/other.py"], max_tokens=2000)
    assert "You edited files you were not shown" in extra and "   1|def clip(text, cap):" in extra
    assert unseen_files(tmp_path, reply, shown=["pkg/context.py"], max_tokens=2000) == ""


def test_a_repair_with_an_unseen_file_stays_inside_the_first_prompt(
    settings: Settings, repo: Path
) -> None:
    """M7 rerun: the repair stacked context.py on the full context and the coder got a 400."""
    (repo / "big.py").write_text("".join(f"def f{i}():\n    return {i}\n\n" for i in range(900)))
    for i in range(6):  # more calc files than the prompt holds, so pkg/ is never shown
        (repo / f"calc_{i}.py").write_text(
            f"from calc import add\n\n\ndef add_{i}(a):\n    return add(a, {i})\n"
        )
    (repo / "pkg").mkdir()
    (repo / "pkg" / "deep_inside.py").write_text("def clip(text, cap):\n    return text\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    miss = (
        "pkg/deep_inside.py\n<<<<<<< SEARCH\ndef clip(t):\n=======\ndef clip(t, c):\n"
        ">>>>>>> REPLACE\n"
    )
    prompts: list[str] = []

    def coder(model: str, msgs: list) -> str:  # type: ignore[type-arg]
        prompts.append(msgs[1]["content"])
        return miss if len(prompts) == 1 else GOOD

    o = Orchestrator(
        settings,
        FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, msgs: JUDGE}),
        repo=repo,
        checks=PYTEST,
    )
    run = o.start("add sub(a, b) to calc and use big", lane="coder")
    first, repair = prompts
    assert "def f899" in first or "cut to fit" in first  # the first prompt carried big.py
    assert "   1|def clip(text, cap):" in repair and "def f1()" not in repair
    assert len(repair) < len(first)
    assert run.repaired and run.state == "ready"


def test_a_failed_coder_run_is_retried_on_deep(settings: Settings, repo: Path) -> None:
    def boom(model: str, msgs: list) -> str:  # type: ignore[type-arg]
        raise RuntimeError("400 Bad Request: the request exceeds the available context size")

    settings.lanes["coder"].retry_when = "now"
    o = Orchestrator(
        settings,
        FakeBackend(
            scripts={
                "pablo/coder": boom,
                "pablo/deep": lambda m, msgs: GOOD,
                "pablo/judge": lambda m, msgs: JUDGE,
            }
        ),
        repo=repo,
        checks=PYTEST,
    )
    o.retry_inline = True
    run = o.start("add sub(a, b)", lane="coder")
    assert run.state == "failed" and run.retry_run
    assert o.get(run.retry_run).state == "ready"
