"""The holdout suite checks itself: each hidden test fails on the seed and passes on a real fix.

This file reads the holdout tasks only to prove they're sound. Nothing here prints or keeps a
model's results on them (see evals/holdout/tasks.yaml).
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from pablo.evals import HOLDOUT, SuiteTask, hidden_check, load_suite, materialize
from pablo.sandbox import LocalSandbox
from pablo.verify import IMPORTS_CHECK, run_check

TASKS = load_suite(HOLDOUT)
PY = sys.executable


def visible_ok(repo: Path) -> tuple[bool, str]:
    proc = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo,
                          capture_output=True, text=True)  # fmt: skip
    return proc.returncode == 0, proc.stdout[-800:]


def test_holdout_shape() -> None:
    assert len(TASKS) >= 10 and len({t.id for t in TASKS}) == len(TASKS)
    assert all(t.id.startswith("holdout-") for t in TASKS)
    assert set(Counter(t.refs for t in TASKS)) == {"file", "symbol", "none"}
    assert sum(len(t.solution) > 1 for t in TASKS) >= 6  # most need more than one file
    main = {t.id for t in load_suite()}
    assert not main & {t.id for t in TASKS}


def test_the_seed_is_green(tmp_path: Path) -> None:
    ok, out = visible_ok(materialize(TASKS[0], tmp_path, HOLDOUT))
    assert ok, out


@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
def test_hidden_test_needs_a_real_fix(tmp_path: Path, task: SuiteTask) -> None:
    repo = materialize(task, tmp_path, HOLDOUT)
    box = LocalSandbox()
    base, _ = hidden_check(task, repo, "", box, python=PY)
    assert not base, "the hidden test already passes on the seed repo"
    fixed, out = hidden_check(task, repo, "", box, files=task.solution, python=PY)
    assert fixed, out
    for rel, text in task.solution.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    ok, out = visible_ok(repo)
    assert ok, f"the reference solution breaks the visible tests:\n{out}"
    imports = run_check("imports", [PY, *IMPORTS_CHECK[1:]], repo)
    assert imports.passed, imports.output_tail


def test_a_holdout_eval_prints_and_keeps_totals_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from typer.testing import CliRunner

    from pablo import evals
    from pablo.cli import app

    out = tmp_path / "holdout-results.jsonl"
    monkeypatch.setattr(evals, "HOLDOUT_RESULTS", out)
    monkeypatch.setattr(evals.append_sealed, "__defaults__", (out,))
    monkeypatch.setenv("PABLO_HOME", str(tmp_path / "home"))
    runner = CliRunner()
    shown = runner.invoke(app, ["eval", "--set", "holdout", "--fake"])
    assert shown.exit_code == 0, shown.output
    assert "Passed per run" in shown.output
    assert not any(t.id in shown.output for t in TASKS)  # no task ids on screen
    refused = runner.invoke(app, ["eval", "--set", "holdout", "--only", TASKS[0].id, "--fake"])
    assert refused.exit_code != 0

    report = evals.SuiteReport(lane="coder", model="m", results=[], commit="c")
    evals.append_sealed(report, out)
    row = json.loads(out.read_text().splitlines()[-1])
    assert row["set"] == "holdout" and isinstance(row["false_ready"], int) and "results" not in row


def test_practice_never_touches_the_holdout() -> None:
    from pablo.practice import suite_names

    # Practice skips every identifier a holdout task or solution mentions.
    assert {"drop_lowest", "late_cap", "percent_text"} <= suite_names()
