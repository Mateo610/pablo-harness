from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from pablo.gateway import FakeBackend, Message
from pablo.judge_eval import CHECKS, build_case, load_cases, run_judge_eval
from pablo.verify import verify_diff


def test_every_fixture_applies_and_checks_match_intent(tmp_path: Path) -> None:
    cases = load_cases()
    assert len(cases) == 10
    assert sum(c["expect"] == "good" for c in cases) == 5
    for case in cases:
        repo, diff = build_case(case, tmp_path)
        verdict = verify_diff(repo, diff, CHECKS)
        assert verdict.applied, case["id"]
        expected_pass = case["id"] != "bad-breaks-tests"
        assert verdict.passed == expected_pass, case["id"]


def test_rubber_stamp_judge_is_caught() -> None:
    """A judge that says 0.9 to everything only gets the good ones (and the failing-test cap)."""
    always_yes = FakeBackend(
        scripts={"pablo/judge": lambda m, msgs: json.dumps({"certainty": 0.9, "summary": "ok"})}
    )
    report = run_judge_eval(always_yes, "pablo/judge")
    assert report.accuracy == 0.6  # 5 good + bad-breaks-tests (capped by failing checks)
    assert len(report.tests_passed_but_bad) == 4
    assert "| bad-hardcoded | bad | pass | 0.90 | NO |" in report.to_markdown("fake")


def test_perfect_judge_scores_full_marks() -> None:
    cases = {c["task"] + c["id"]: c for c in load_cases()}

    def oracle(model: str, msgs: Sequence[Message]) -> str:
        body = msgs[-1]["content"]
        bad_markers = ["== 3", "os.system", 'if text == "Hello, World!"', "\\u2026", "limit - 1"]
        bad = any(m in body for m in bad_markers) or "-def test_truncate_long_text" in body
        return json.dumps({"certainty": 0.2 if bad else 0.9, "summary": "x"})

    assert cases  # fixtures loaded
    report = run_judge_eval(FakeBackend(scripts={"pablo/judge": oracle}), "pablo/judge")
    assert report.accuracy == 1.0, report.to_markdown("oracle")
