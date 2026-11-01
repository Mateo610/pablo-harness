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
from pablo.verify import apply_diff, diff_stats, verify_diff

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


def test_extract_diff_drops_invented_index_lines() -> None:
    raw = coder_reply(
        "diff --git a/calc.py b/calc.py\nindex 1234567..abcdefg 100644\n--- a/calc.py\n"
    )
    _, diff = extract_diff(raw)
    assert "index " not in diff
    assert diff.endswith("\n")


def test_extract_diff_restores_blank_context_lines() -> None:
    raw = coder_reply(
        "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n"
        "@@ -1,1 +1,2 @@\n context\n\n+added\n"
    )
    _, diff = extract_diff(raw)
    assert "\n \n+added\n" in diff


def test_extract_diff_fixes_hunk_counts() -> None:
    raw = coder_reply("--- a/calc.py\n+++ b/calc.py\n@@ -1,1 +1,1 @@\n context\n+added\n")
    _, diff = extract_diff(raw)
    assert "@@ -1,1 +1,2 @@" in diff


def test_phantom_blank_context_still_applies(repo: Path) -> None:
    diff = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,3 +1,6 @@\n"
        " def add(a, b):\n"
        "     return a + b\n"
        " \n"
        "+def sub(a, b):\n"
        "+    return a - b\n"
    )
    ok, err = apply_diff(repo, diff, check_only=True)
    assert ok, err


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


TWO_HUNK_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@
+# calc module
 def add(a, b):
     return a + b
diff --git a/notes.md b/notes.md
new file mode 100644
--- /dev/null
+++ b/notes.md
@@ -0,0 +1 @@
+notes
"""


def test_hunk_parse_and_select() -> None:
    from pablo.hunks import all_ids, parse, select

    files = parse(TWO_HUNK_DIFF)
    assert [f.path for f in files] == ["calc.py", "notes.md"]
    assert all_ids(TWO_HUNK_DIFF) == ["0:0", "1:0"]
    only_notes = select(TWO_HUNK_DIFF, {"1:0"})
    assert "calc.py" not in only_notes and "+notes" in only_notes
    with pytest.raises(ValueError):
        select(TWO_HUNK_DIFF, {"9:9"})


def test_partial_approve_applies_kept_hunks_and_never_trains(
    settings: Settings, repo: Path
) -> None:
    orch = make_orch(settings, repo, coder_reply(TWO_HUNK_DIFF), 0.9)
    run = orch.start("add a note", lane="coder")
    orch.approve(run.id, ["1:0"])
    assert (repo / "notes.md").is_file()
    assert "# calc module" not in (repo / "calc.py").read_text()
    assert orch.get(run.id).applied_hunks == ["1:0"]
    assert TraceStore(settings.traces_dir).export_training(settings.home / "t.jsonl") == 0


def git_diff_of(repo: Path, edits: dict[str, str]) -> str:
    """Make edits, capture `git diff`, then restore the files."""
    import subprocess

    for rel, text in edits.items():
        (repo / rel).write_text(text)
    diff = subprocess.run(
        ["git", "diff"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout
    subprocess.run(["git", "checkout", "--", "."], cwd=repo, check=True)
    return diff


def test_partial_approve_blocked_when_checks_fail(settings: Settings, repo: Path) -> None:
    from pablo.orchestrator import PartialChecksFailed

    calc = (repo / "calc.py").read_text() + "\n\ndef sub(a, b):\n    return a - b\n"
    tests = (repo / "test_calc.py").read_text() + (
        "\n\ndef test_sub():\n    from calc import sub\n\n    assert sub(3, 2) == 1\n"
    )
    diff = git_diff_of(repo, {"calc.py": calc, "test_calc.py": tests})
    orch = make_orch(settings, repo, coder_reply(diff), 0.9)
    run = orch.start("add sub with a test", lane="coder")
    assert run.state == "ready"
    with pytest.raises(PartialChecksFailed):
        orch.approve(run.id, ["1:0"])  # keep the test, drop the function: tests fail
    assert "test_sub" not in (repo / "test_calc.py").read_text()
    orch.approve(run.id, ["1:0"], force=True)
    assert "test_sub" in (repo / "test_calc.py").read_text()


def test_events_are_persisted_for_replay(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo, coder_reply(GOOD_DIFF), 0.9)
    run = orch.start("add sub", lane="coder")
    fresh = Orchestrator(settings, FakeBackend(), repo=repo, checks=CHECKS)
    kinds = [e["kind"] for e in fresh.events(run.id)]
    assert kinds[0] == "route" and "evidence" in kinds and kinds[-1] == "finished"


def test_compare_runs_both_and_approving_one_rejects_other(settings: Settings, repo: Path) -> None:
    backend = FakeBackend(
        scripts={
            "pablo/coder": [coder_reply(GOOD_DIFF)],
            "pablo/deep": ["A different approach, explained."],
            "pablo/judge": [judge_reply(0.9)],
        }
    )
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    a, b = orch.compare("add sub", ["coder", "deep"])
    assert a.group == b.group and a.group is not None
    orch.approve(a.id)
    assert orch.get(b.id).state == "rejected"
    with pytest.raises(PermissionError):
        orch.compare("x", ["coder", "claude"])


def test_unnamed_task_shows_source_files(settings: Settings, repo: Path) -> None:
    backend = FakeBackend(
        scripts={"pablo/coder": [coder_reply(GOOD_DIFF)], "pablo/judge": [judge_reply(0.9)]}
    )
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    orch.start("add a function mul(a, b) to the module with a test", lane="coder")
    user = backend.calls[0][1][-1]["content"]
    assert "def add(a, b):" in user
    assert "def test_add" in user


def test_unappliable_diff_gets_one_repair(settings: Settings, repo: Path) -> None:
    broken = GOOD_DIFF.replace("return a + b", "return a * b")  # wrong context
    backend = FakeBackend(
        scripts={
            "pablo/coder": [coder_reply(broken), coder_reply(GOOD_DIFF)],
            "pablo/judge": [judge_reply(0.9)],
        }
    )
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    run = orch.start("add sub", lane="coder")
    assert run.repaired and run.state == "ready"
    repair_call = backend.calls[1][1]
    assert "did not apply" in repair_call[-1]["content"]


def test_repair_happens_only_once(settings: Settings, repo: Path) -> None:
    broken = GOOD_DIFF.replace("return a + b", "return a * b")
    backend = FakeBackend(
        scripts={
            "pablo/coder": [coder_reply(broken), coder_reply(broken)],
            "pablo/judge": [judge_reply(0.9)],
        }
    )
    run = Orchestrator(settings, backend, repo=repo, checks=CHECKS).start("add sub", lane="coder")
    assert run.state == "needs_you" and not (run.verdict or {}).get("applied")
    assert sum(1 for model, _ in backend.calls if model == "pablo/coder") == 2
