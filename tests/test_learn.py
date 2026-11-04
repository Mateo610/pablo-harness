"""Learning loop: refuse Claude, dedupe/holdout, gated promotion."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pablo.learn import (
    ClaudeInTrainingError,
    Promotion,
    assert_no_claude,
    baseline_pass_rate,
    count_new_since,
    prepare_training,
    promote_adapter,
    reject_promotion,
    save_promotion,
    should_run_nightly,
)
from pablo.traces import Trace, TraceStore

ROOT = Path(__file__).resolve().parents[1]
TRAIN_SCRIPT = ROOT / "scripts" / "train_lora.py"


def _row(lane: str, prompt: str, answer: str = "ok") -> dict:
    return {
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ],
        "lane": lane,
    }


def test_assert_no_claude_raises() -> None:
    from pablo.learn import TrainingRow

    parsed = [TrainingRow.from_export(r) for r in (_row("coder", "a"), _row("claude", "b"))]
    with pytest.raises(ClaudeInTrainingError):
        assert_no_claude(parsed)


def test_prepare_training_dedupes_and_holds_out(tmp_path: Path) -> None:
    export = tmp_path / "approved.jsonl"
    # 12 unique prompts, each duplicated once → 12 unique after dedupe, 10% holdout = 1
    rows = [_row("coder", f"prompt-{i % 12}") for i in range(24)]
    export.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    prepared = prepare_training(export, tmp_path / "out", seed=1)
    assert prepared.train_n + prepared.holdout_n == 12
    assert prepared.skipped_dupes == 12
    assert prepared.holdout_n == 1
    assert prepared.train_n == 11


def test_prepare_refuses_claude(tmp_path: Path) -> None:
    export = tmp_path / "approved.jsonl"
    export.write_text(json.dumps(_row("claude", "x")) + "\n", encoding="utf-8")
    with pytest.raises(ClaudeInTrainingError):
        prepare_training(export, tmp_path / "out")


def test_train_lora_script_refuses_claude(tmp_path: Path) -> None:
    data = tmp_path / "in.jsonl"
    data.write_text(json.dumps(_row("claude", "nope")) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(TRAIN_SCRIPT), "--data", str(data), "--out", str(out), "--skip-train"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "escalation" in (proc.stderr + proc.stdout).lower()


def test_train_lora_script_prepares(tmp_path: Path) -> None:
    data = tmp_path / "in.jsonl"
    lines = [json.dumps(_row("coder", f"p{i}")) for i in range(12)]
    data.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(TRAIN_SCRIPT), "--data", str(data), "--out", str(out), "--skip-train"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "train.jsonl").is_file()
    assert (out / "holdout.jsonl").is_file()


def test_promotion_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PABLO_DATA", str(tmp_path / "pdata"))
    from pablo import learn as learn_mod

    monkeypatch.setattr(learn_mod, "PABLO_DATA", tmp_path / "pdata")
    adapters = tmp_path / "pdata" / "adapters"
    candidate = adapters / "coder-candidate"
    candidate.mkdir(parents=True)
    (candidate / "adapter.gguf").write_bytes(b"fake")

    weak = Promotion(
        id="weak",
        created_at="t",
        adapter=str(candidate),
        baseline_pass_rate=0.82,
        candidate_pass_rate=0.80,
        baseline_commit="abc",
        candidate_wall_s=1.0,
        beats_baseline=False,
        state="held",
    )
    save_promotion(weak, tmp_path / "home")
    assert learn_mod.load_pending(tmp_path / "home") is None

    strong = Promotion(
        id="strong",
        created_at="t",
        adapter=str(candidate),
        baseline_pass_rate=0.82,
        candidate_pass_rate=0.90,
        baseline_commit="abc",
        candidate_wall_s=1.0,
        beats_baseline=True,
        state="pending",
        runs=3,
    )
    save_promotion(strong, tmp_path / "home")
    pending = learn_mod.load_pending(tmp_path / "home")
    assert pending is not None and pending.id == "strong"
    done = promote_adapter(tmp_path / "home")
    assert done.state == "promoted"
    assert (adapters / "coder-active" / "adapter.gguf").is_file()
    assert learn_mod.load_pending(tmp_path / "home") is None


def test_reject_promotion(tmp_path: Path) -> None:
    promo = Promotion(
        id="r1",
        created_at="t",
        adapter="/x",
        baseline_pass_rate=0.5,
        candidate_pass_rate=0.9,
        baseline_commit="",
        candidate_wall_s=0.0,
        beats_baseline=True,
        state="pending",
    )
    save_promotion(promo, tmp_path)
    reject_promotion(tmp_path, note="nope")
    assert (tmp_path / "promotions" / "pending.json").is_file() is False
    saved = json.loads((tmp_path / "promotions" / "r1.json").read_text(encoding="utf-8"))
    assert saved["state"] == "rejected"


def test_nightly_threshold(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    base = dict(model="m", messages=[{"role": "user", "content": "hi"}], output="yo")
    for i in range(3):
        store.append(
            Trace(run_id=f"r{i}", lane="coder", lane_train_ok=True, **base)  # type: ignore[arg-type]
        )
        store.record_decision(f"r{i}", "approved")
    ok, reason, n = should_run_nightly(store, tmp_path)
    assert not ok and n == 3 and "50" in reason


def test_count_new_since_marker(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    base = dict(model="m", messages=[{"role": "user", "content": "hi"}], output="yo")
    store.append(Trace(run_id="a", lane="coder", lane_train_ok=True, **base))  # type: ignore[arg-type]
    store.record_decision("a", "approved")
    marker = tmp_path / "last"
    marker.write_text(str(10**12), encoding="utf-8")  # far future → nothing new
    assert count_new_since(store, marker) == 0


def test_baseline_pass_rate(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    rows = [
        {"lane": "coder", "pass_rate": 0.8, "tasks": 36, "commit": "a"},
        {"lane": "coder", "pass_rate": 0.84, "tasks": 36, "commit": "a"},
        {"lane": "coder", "pass_rate": 0.82, "tasks": 36, "commit": "a"},
        {"lane": "deep", "pass_rate": 0.94, "tasks": 36, "commit": "a"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    mean, commit = baseline_pass_rate(path)
    assert mean == pytest.approx(0.82, abs=0.001)
    assert commit == "a"


def _report(lane: str, passes: int, false_ready: int = 0, tasks: int = 36) -> Any:
    from pablo.evals import SuiteReport, TaskResult

    rows = []
    for i in range(tasks):
        ok = i < passes
        broken_but_ready = not ok and i < passes + false_ready
        rows.append(
            TaskResult(
                id=f"t{i}", refs="file", kind="bugfix",
                state="ready" if ok or broken_but_ready else "needs_you",
                has_diff=True, clean=True, hidden_passed=ok, certainty=0.9, wall_s=1.0,
            )
        )  # fmt: skip
    return SuiteReport(lane=lane, model=lane, results=rows)


def test_the_gate_needs_three_runs_a_real_lift_and_no_more_false_ready() -> None:
    from pablo.learn import gate

    coder = [_report("coder", n) for n in (29, 31, 27)]  # 80% ± 6%
    assert not gate(coder[:1], [_report("c", 31)]).beats  # one run each: never
    small = gate(coder, [_report("c", n) for n in (30, 31, 31)])
    assert not small.beats and "within the spread" in small.reason
    big = gate(coder, [_report("c", n) for n in (34, 35, 34)])
    assert big.beats and big.runs == 3
    riskier = gate(coder, [_report("c", n, false_ready=3) for n in (34, 35, 34)])
    assert not riskier.beats and "false ready" in riskier.reason


def test_promote_refuses_a_single_run_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The M8 smoke test's 31/36 against a mean from another night is not a result."""
    from pablo import learn as learn_mod

    monkeypatch.setattr(learn_mod, "PABLO_DATA", tmp_path / "pdata")
    candidate = tmp_path / "pdata" / "adapters" / "coder-candidate"
    candidate.mkdir(parents=True)
    (candidate / "adapter.gguf").write_bytes(b"fake")
    old = Promotion(
        id="coder-20261104-seq256", created_at="t", adapter=str(candidate),
        baseline_pass_rate=0.842, candidate_pass_rate=0.861, baseline_commit="8b6188f",
        candidate_wall_s=1425.0, beats_baseline=True, state="pending",
    )  # fmt: skip
    save_promotion(old, tmp_path / "home")
    with pytest.raises(ValueError, match="needs 3"):
        promote_adapter(tmp_path / "home")


def test_eval_suite_tasks_never_reach_training(tmp_path: Path) -> None:
    from pablo.evals import load_suite

    task = load_suite()[0].task
    rows = [
        {"lane": "coder", "messages": [{"role": "user", "content": f"Task:\n{task}"},
                                       {"role": "assistant", "content": "x"}]},
        {"lane": "coder", "messages": [{"role": "user", "content": "rename a variable"},
                                       {"role": "assistant", "content": "y"}]},
    ]  # fmt: skip
    export = tmp_path / "approved.jsonl"
    export.write_text("".join(json.dumps(r) + "\n" for r in rows))
    prepared = prepare_training(export, tmp_path / "out")
    assert prepared.skipped_eval == 1 and prepared.train_n == 1
