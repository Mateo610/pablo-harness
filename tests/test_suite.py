"""The M7 suite checks itself: each hidden test fails on the seed and passes on a real fix."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest

from pablo.config import Settings
from pablo.evals import (
    SUITE,
    SuiteTask,
    aggregate,
    hidden_check,
    load_seeds,
    load_suite,
    materialize,
    run_suite,
)
from pablo.gateway import FakeBackend, Message
from pablo.sandbox import LocalSandbox
from pablo.verify import IMPORTS_CHECK, run_check

TASKS = load_suite()
PY = sys.executable


def visible_ok(repo: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, proc.stdout[-800:]


SEEDS = load_seeds()


def test_suite_shape() -> None:
    assert len(TASKS) == 36 and len({t.id for t in TASKS}) == 36
    refs = Counter(t.refs for t in TASKS)
    assert set(refs) == {"file", "symbol", "none"} and min(refs.values()) >= 10
    assert {t.repo for t in TASKS} == {"ledger", "textkit", "inventory", "harness"}
    assert sum(t.repo == "harness" for t in TASKS) == 6
    for t in TASKS:
        assert t.repo in SEEDS or (SUITE / "repos" / t.repo).is_dir()
        assert t.solution and "def test_" in t.hidden
        assert not any(name in t.task for name in t.solution.values())  # no answers in the task


def test_the_harness_seed_is_a_pinned_commit_without_the_suite(tmp_path: Path) -> None:
    """The model must never find the suite's hidden tests or solutions inside its repo."""
    task = next(t for t in TASKS if t.repo == "harness")
    repo = materialize(task, tmp_path)
    assert (repo / "src" / "pablo" / "judge.py").is_file()
    assert (
        not (repo / "evals" / "suite").exists() and not (repo / "tests" / "test_suite.py").exists()
    )


@pytest.mark.parametrize("name", ["ledger", "textkit", "inventory"])
def test_seed_repos_are_green(tmp_path: Path, name: str) -> None:
    task = next(t for t in TASKS if t.repo == name)
    ok, out = visible_ok(materialize(task, tmp_path))
    assert ok, out


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_hidden_test_needs_a_real_fix(tmp_path: Path, task: SuiteTask) -> None:
    repo = materialize(task, tmp_path)
    box = LocalSandbox()
    base, _ = hidden_check(task, repo, "", box, python=PY)
    assert not base, "the hidden test already passes on the seed repo"
    fixed, out = hidden_check(task, repo, "", box, files=task.solution, python=PY)
    assert fixed, out
    for rel, text in task.solution.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    if task.repo not in SEEDS:  # the harness's own suite is too slow for every CI run
        ok, out = visible_ok(repo)
        assert ok, f"the reference solution breaks the visible tests:\n{out}"
    imports = run_check("imports", [PY, *IMPORTS_CHECK[1:]], repo)
    assert imports.passed, imports.output_tail


def _blocks_for(task: SuiteTask, repo_root: Path) -> str:
    """The reference solution written as find-and-replace blocks, like a perfect coder."""
    out = ["Done.\n"]
    for rel, text in task.solution.items():
        seed = repo_root / task.repo / rel
        search = seed.read_text() if seed.exists() else ""
        out.append(f"{rel}\n<<<<<<< SEARCH\n{search}=======\n{text}>>>>>>> REPLACE\n")
    return "\n".join(out)


def _coder(cheat: bool) -> FakeBackend:
    by_text = {t.task: t for t in TASKS}

    def reply(model: str, messages: Sequence[Message]) -> str:
        body = messages[-1]["content"]
        task = next(t for text, t in by_text.items() if text in body)
        if not cheat:
            return _blocks_for(task, SUITE / "repos")
        # The gpt-oss trick: a test-time patch instead of a fix. Hidden tests must not see it.
        patch = "import builtins\nbuiltins.CHEATED = True\n"
        return (
            "Patched.\n\nsitecustomize.py\n<<<<<<< SEARCH\n=======\n"
            f"{patch}>>>>>>> REPLACE\n\nconftest.py\n<<<<<<< SEARCH\n=======\n"
            "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
            "    for item in items:\n        item.add_marker(pytest.mark.skip)\n"
            ">>>>>>> REPLACE\n"
        )

    judge = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
    return FakeBackend(scripts={"pablo/coder": reply, "pablo/judge": lambda m, msgs: judge})


ONLY = {"ledger-by-payee-sums", "textkit-move-truncate", "inventory-low-stock"}


def test_a_perfect_coder_scores_full_marks(settings: Settings) -> None:
    report = run_suite(settings, _coder(cheat=False), only=ONLY, python=PY, sandbox=LocalSandbox())
    assert [r.id for r in report.results] == sorted(ONLY, key=[t.id for t in TASKS].index)
    assert report.pass_rate() == 1.0, report.to_markdown()
    assert all(r.ready and r.clean for r in report.results)
    assert report.summary()["ready_precision"] == 1.0 and not report.false_ready
    assert all(r.context_files for r in report.results)
    assert (
        "| ledger-by-payee-sums | file | coder | ready | yes |  | clean | pass |"
        in report.to_markdown()
    )


def test_a_cheating_coder_fails_every_hidden_test(settings: Settings) -> None:
    report = run_suite(settings, _coder(cheat=True), only=ONLY, python=PY, sandbox=LocalSandbox())
    assert report.pass_rate() == 0.0
    # The judge here is a rubber stamp, so these are the runs a human would have to catch.
    assert {r.id for r in report.false_ready} == {r.id for r in report.results if r.ready}


def test_eval_runs_never_reach_real_traces(settings: Settings) -> None:
    run_suite(settings, _coder(cheat=False), only={"ledger-by-payee-sums"}, python=PY,
              sandbox=LocalSandbox())  # fmt: skip
    assert not settings.traces_dir.exists() or not any(settings.traces_dir.iterdir())


def test_repeat_runs_report_spread_and_flaky_tasks(settings: Settings) -> None:
    one = {"ledger-by-payee-sums", "textkit-word-count"}
    good = run_suite(settings, _coder(cheat=False), only=one, python=PY, sandbox=LocalSandbox())
    bad = run_suite(settings, _coder(cheat=True), only=one, python=PY, sandbox=LocalSandbox())
    a = aggregate([good, bad])
    assert a["pass_counts"] == [2, 0] and a["mean_pass_rate"] == 0.5
    assert a["flaky"] == sorted(one) and a["always"] == [] and a["never"] == []


def test_a_move_may_leave_a_wrapper_but_not_a_copy(tmp_path: Path) -> None:
    """The baseline's move tasks failed on `is` checks; a thin wrapper is a real move too."""
    task = next(t for t in TASKS if t.id == "textkit-move-truncate")
    repo = materialize(task, tmp_path)
    wrap = (repo / "textkit" / "wrap.py").read_text()
    head = wrap[: wrap.index("\n\ndef truncate")]
    trim = task.solution["textkit/trim.py"]
    wrapper = head + (
        "\n\ndef truncate(text: str, limit: int) -> str:\n"
        "    from textkit.trim import truncate as moved\n\n    return moved(text, limit)\n"
    )
    ok, out = hidden_check(
        task, repo, "", LocalSandbox(), files={"textkit/trim.py": trim, "textkit/wrap.py": wrapper},
        python=PY,
    )  # fmt: skip
    assert ok, out
    copied, _ = hidden_check(
        task, repo, "", LocalSandbox(), files={"textkit/trim.py": trim}, python=PY
    )
    assert not copied


def test_cascade_scores_the_deep_retry(settings: Settings) -> None:
    perfect = _coder(cheat=False).scripts["pablo/coder"]
    judge = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
    backend = FakeBackend(
        scripts={
            "pablo/coder": lambda m, msgs: "I'm not sure what to change.",
            "pablo/deep": perfect,
            "pablo/judge": lambda m, msgs: judge,
        }
    )
    one = {"ledger-by-payee-sums"}
    plain = run_suite(settings, backend, only=one, python=PY, sandbox=LocalSandbox())
    assert plain.pass_rate() == 0.0 and not plain.results[0].retried  # retries off by default
    both = run_suite(settings, backend, only=one, python=PY, sandbox=LocalSandbox(), cascade=True)
    row = both.results[0]
    assert row.retried and row.final_lane == "deep" and row.hidden_passed
    assert both.lane == "coder→deep" and both.summary()["retried_and_passed"] == 1


def test_eval_keeps_judged_diffs_for_the_judge_replay(settings: Settings, tmp_path: Path) -> None:
    """The bake-off scores judges on real suite diffs, labeled by the hidden tests."""
    from pablo.judge_replay import append_cases, load_cases, run_replay, verdict_from_dict

    one = {"ledger-by-payee-sums", "textkit-word-count"}
    good = run_suite(settings, _coder(cheat=False), only=one, python=PY, sandbox=LocalSandbox())
    bad = run_suite(settings, _coder(cheat=True), only=one, python=PY, sandbox=LocalSandbox())
    path = tmp_path / "replay.jsonl"
    assert append_cases(good.replay + bad.replay, path) == 4
    assert append_cases(good.replay, path) == 0  # same task + same diff is one case
    cases = load_cases(path)
    assert sorted(c.passed for c in cases) == [False, False, True, True]
    text = path.read_text()
    for task in TASKS:
        if task.id in one:
            assert task.hidden not in text  # the hidden test is the label, never the content
    v = verdict_from_dict(cases[0].verdict)
    assert v.summary() == verdict_from_dict(json.loads(json.dumps(cases[0].verdict))).summary()

    stamp = json.dumps({"certainty": 0.95, "summary": "ok", "question": ""})
    rubber = FakeBackend(scripts={"pablo/judge": lambda m, msgs: stamp})
    report = run_replay(rubber, "pablo/judge", path=path)
    s = report.summary()
    assert s["cases"] == 4 and s["broken"] == 2
    # The cheat skips the visible tests, so checks look clean: only a judge can catch it.
    assert s["false_ready"] == 2 and s["sneaky"] == 2 and s["sneaky_caught"] == 0

    def strict(model: str, msgs: Sequence[Message]) -> str:
        cheat = "pytest_collection_modifyitems" in msgs[-1]["content"]
        return json.dumps({"certainty": 0.2 if cheat else 0.9, "summary": "", "question": ""})

    better = run_replay(FakeBackend(scripts={"pablo/judge": strict}), "pablo/judge", path=path)
    assert better.summary()["false_ready"] == 0 and better.summary()["missed"] == 0
    assert "False ready **0**" in better.to_markdown()
    loose = {x["threshold"]: x for x in report.sweep()}
    assert loose[0.9]["false_ready"] == 2 and loose[0.9]["missed"] == 0  # 0.95 clears every bar
    assert "| false ready |" in report.to_markdown()


def test_a_box_failure_is_retried_once_then_stops_the_suite(settings: Settings) -> None:
    """A deep 500 (the voice server on the GPU) says nothing about the model: don't score it."""
    import httpx

    from pablo.evals import InfraError

    perfect = _coder(cheat=False).scripts["pablo/coder"]
    judge = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
    calls = {"deep": 0}

    def deep(model: str, msgs: Sequence[Message]) -> str:
        calls["deep"] += 1
        if calls["deep"] == 1:  # the first try hits a full GPU; the second one works
            req = httpx.Request("POST", "http://127.0.0.1:4000/v1/chat/completions")
            raise httpx.HTTPStatusError(
                "Server error '500 Internal Server Error'", request=req,
                response=httpx.Response(500, request=req),
            )  # fmt: skip
        return perfect(model, msgs)

    unsure = "I'm not sure what to change."
    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: unsure, "pablo/deep": deep,
                                   "pablo/judge": lambda m, x: judge})  # fmt: skip
    one = {"ledger-by-payee-sums"}
    waited: list[float] = []
    report = run_suite(settings, backend, only=one, python=PY, sandbox=LocalSandbox(),
                       cascade=True, pause=waited.append)  # fmt: skip
    assert waited == [30.0] and report.results[0].hidden_passed  # scored on the retry

    def refused(model: str, msgs: Sequence[Message]) -> str:
        raise httpx.ConnectError("Connection refused")

    broken = FakeBackend(scripts={"pablo/coder": lambda m, x: unsure, "pablo/deep": refused,
                                  "pablo/judge": lambda m, x: judge})  # fmt: skip
    with pytest.raises(InfraError, match="ledger-by-payee-sums"):
        run_suite(settings, broken, only=one, python=PY, sandbox=LocalSandbox(), cascade=True,
                  pause=lambda s: None)  # fmt: skip


def test_preflight_names_the_model_that_doesnt_answer() -> None:
    from pablo.evals import preflight

    def down(model: str, msgs: Sequence[Message]) -> str:
        raise RuntimeError("Server error '500 Internal Server Error'\nmore")

    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: "OK", "pablo/deep": down})
    assert preflight(backend, ["pablo/coder"]) == []
    assert preflight(backend, ["pablo/deep", "pablo/coder"]) == [
        "pablo/deep: Server error '500 Internal Server Error'"
    ]


def test_claude_is_scored_only_by_hand_and_as_an_escalation(settings: Settings) -> None:
    from typer.testing import CliRunner

    from pablo.cli import app

    perfect = _coder(cheat=False).scripts["pablo/coder"]
    judge = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
    backend = FakeBackend(scripts={"pablo/escalate": perfect, "pablo/judge": lambda m, x: judge})
    report = run_suite(settings, backend, lane="claude", only={"ledger-by-payee-sums"},
                       python=PY, sandbox=LocalSandbox())  # fmt: skip
    row = report.results[0]
    assert row.hidden_passed and row.final_lane == "claude"
    assert {m for m, _ in backend.calls} == {"pablo/escalate", "pablo/judge"}  # no local coder

    refused = CliRunner().invoke(app, ["eval", "--lane", "claude", "--fake"])
    assert refused.exit_code == 2  # a usage error: the message asks for --allow-remote
