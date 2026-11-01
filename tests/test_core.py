from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pablo.config import Settings
from pablo.gateway import FakeBackend
from pablo.judge import parse_judgement
from pablo.orchestrator import Orchestrator, extract_diff
from pablo.plan import Plan, starter_plan
from pablo.router import Router, estimate_complexity
from pablo.traces import Trace, TraceStore
from pablo.verify import diff_stats, verify_diff

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}

GOOD_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,6 @@
 def add(a, b):
     return a + b
+
+
+def sub(a, b):
+    return a - b
"""

BAD_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


def coder_reply(diff: str) -> str:
    return f"Here is the change.\n\n```diff\n{diff}```\n"


def judge_reply(certainty: float) -> str:
    return json.dumps({"certainty": certainty, "summary": "looks fine", "question": ""})


# ---- config -------------------------------------------------------------------
def test_escalation_lane_never_trains(settings: Settings) -> None:
    claude = settings.lane("claude")
    assert claude.train_ok is False
    assert claude.requires_approval is True


# ---- router -------------------------------------------------------------------
def test_router_keywords_pick_coder(settings: Settings) -> None:
    decision = Router(settings).route("fix the failing test in the api module")
    assert decision.lane == "coder"


def test_router_keywords_pick_media(settings: Settings) -> None:
    assert Router(settings).route("render a 3d mesh of the planter in blender").lane == "media"


def test_router_never_auto_escalates(settings: Settings) -> None:
    backend = FakeBackend(scripts={"pablo/router": ['{"lane": "claude", "confidence": 0.99}']})
    decision = Router(settings, backend).route("something vague")
    assert decision.lane != "claude"


def test_router_uses_model_when_rules_unsure(settings: Settings) -> None:
    backend = FakeBackend(scripts={"pablo/router": ['{"lane": "deep", "confidence": 0.8}']})
    decision = Router(settings, backend).route("think about this for me")
    assert (decision.lane, decision.reason) == ("deep", "router model")


def test_complexity() -> None:
    assert estimate_complexity("rename x") == "quick"
    assert estimate_complexity("redesign the storage layer across the codebase") == "deep"


# ---- verify -------------------------------------------------------------------
def test_diff_stats() -> None:
    assert diff_stats(GOOD_DIFF) == (4, 0, ["calc.py"])


def test_verify_good_diff_passes(repo: Path) -> None:
    verdict = verify_diff(repo, GOOD_DIFF, CHECKS)
    assert verdict.applied and verdict.passed
    assert "def sub" not in (repo / "calc.py").read_text()  # working tree untouched


def test_verify_bad_diff_fails(repo: Path) -> None:
    verdict = verify_diff(repo, BAD_DIFF, CHECKS)
    assert verdict.applied and not verdict.passed


def test_verify_unappliable_diff(repo: Path) -> None:
    verdict = verify_diff(repo, GOOD_DIFF.replace("return a + b", "return a * b"), CHECKS)
    assert not verdict.applied and verdict.error


# ---- judge / parsing -----------------------------------------------------------
def test_extract_diff() -> None:
    explanation, diff = extract_diff(coder_reply(GOOD_DIFF))
    assert explanation == "Here is the change."
    assert diff.startswith("diff --git")


def test_parse_judgement_handles_junk() -> None:
    assert parse_judgement("no json here").certainty == 0.0
    assert parse_judgement('ok {"certainty": 1.7}').certainty == 1.0


# ---- traces -------------------------------------------------------------------
def test_training_rule(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    base = dict(model="m", messages=[{"role": "user", "content": "hi"}], output="yo")
    store.append(Trace(run_id="a", lane="coder", lane_train_ok=True, **base))  # type: ignore[arg-type]
    store.append(Trace(run_id="b", lane="coder", lane_train_ok=True, **base))  # type: ignore[arg-type]
    store.append(Trace(run_id="c", lane="claude", lane_train_ok=True, **base))  # type: ignore[arg-type]
    store.record_decision("a", "approved")
    store.record_decision("b", "rejected")
    store.record_decision("c", "approved")
    out = tmp_path / "train.jsonl"
    assert store.export_training(out) == 1  # only the approved local trace
    assert json.loads(out.read_text().splitlines()[0])["lane"] == "coder"


# ---- plan ---------------------------------------------------------------------
def test_plan_roundtrip_and_next(tmp_path: Path) -> None:
    plan = starter_plan("demo")
    plan.save(tmp_path)
    loaded = Plan.load(tmp_path)
    assert loaded.next_task() is not None and loaded.next_task().id == "1.1"  # type: ignore[union-attr]
    loaded.set_status("1.1", "done")
    loaded.set_status("1.2", "done")
    assert loaded.current_phase() is None


# ---- orchestrator end to end ---------------------------------------------------
def make_orch(settings: Settings, repo: Path, coder: str, certainty: float) -> Orchestrator:
    backend = FakeBackend(
        scripts={
            "pablo/coder": [coder],
            "pablo/judge": [judge_reply(certainty)],
            "pablo/escalate": [coder],
            "pablo/router": [],
        }
    )
    return Orchestrator(settings, backend, repo=repo, checks=CHECKS)


def test_run_ready_then_approve(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo, coder_reply(GOOD_DIFF), 0.94)
    run = orch.start("add a sub function to calc.py", lane="coder")
    assert run.state == "ready" and run.certainty == 0.94
    assert "def sub" not in (repo / "calc.py").read_text()
    orch.approve(run.id)
    assert "def sub" in (repo / "calc.py").read_text()
    exported = TraceStore(settings.traces_dir).export_training(settings.home / "t.jsonl")
    assert exported == 1


def test_low_certainty_needs_you(settings: Settings, repo: Path) -> None:
    run = make_orch(settings, repo, coder_reply(GOOD_DIFF), 0.41).start("add sub", lane="coder")
    assert run.state == "needs_you"


def test_failed_checks_cap_certainty(settings: Settings, repo: Path) -> None:
    run = make_orch(settings, repo, coder_reply(BAD_DIFF), 0.99).start("break add", lane="coder")
    assert run.state == "needs_you"
    assert run.certainty is not None and run.certainty <= 0.3


def test_claude_requires_escalate(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo, coder_reply(GOOD_DIFF), 0.9)
    with pytest.raises(PermissionError):
        orch.start("anything", lane="claude")


def test_escalated_trace_never_trains(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo, coder_reply(GOOD_DIFF), 0.41)
    first = orch.start("add sub", lane="coder")
    orch.backend.scripts["pablo/judge"] = [judge_reply(0.95)]  # type: ignore[attr-defined]
    escalated = orch.escalate(first.id)
    assert escalated.lane == "claude" and escalated.escalated_from == first.id
    orch.approve(escalated.id)
    assert TraceStore(settings.traces_dir).export_training(settings.home / "t.jsonl") == 0


def test_answer_only_run_emits_finished(settings: Settings, repo: Path) -> None:
    backend = FakeBackend(scripts={"pablo/deep": ["Here is a plan, no diff."]})
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    run = orch.start("sketch an architecture", lane="deep")
    kinds = [e.kind for e in orch.bus.history(run.id)]
    assert run.state == "ready" and kinds[-1] == "finished"
