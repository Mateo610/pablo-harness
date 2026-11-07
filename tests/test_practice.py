"""Practice: checkable tasks made from your repo; only fixes that pass its real tests are kept."""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from tests.fakes import FakeBackend

from pablo.config import Settings
from pablo.gateway import Message
from pablo.lessons import Lessons
from pablo.orchestrator import Orchestrator
from pablo.practice import CHEAT, Practice, break_function, find_targets

SHOP = '''def total(prices):
    """Sum of all prices."""
    s = 0
    for p in prices:
        s += p
    return s


def in_range(x, lo, hi):
    """True when lo <= x <= hi."""
    ok = lo <= x
    return ok and x <= hi


def _hidden(x):
    return x
'''
TESTS = """from shop import in_range, total


def test_total():
    assert total([1, 2, 3]) == 6


def test_in_range():
    assert in_range(2, 1, 3) and not in_range(5, 1, 3) and in_range(1, 1, 3)
"""
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
BODY = {"total": "    s = 0\n    for p in prices:\n        s += p\n    return s\n",
        "in_range": "    ok = lo <= x\n    return ok and x <= hi\n"}  # fmt: skip


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    root.mkdir()
    (root / "shop.py").write_text(SHOP)
    (root / "test_shop.py").write_text(TESTS)
    (root / ".pablo").mkdir()
    (root / ".pablo" / "checks.yaml").write_text(
        f"tests: {sys.executable} -m pytest -q\nimports: off\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


def fixer(how: str):  # type: ignore[no-untyped-def]
    """A coder that fixes reimplement tasks right, wrong, or by cheating on the tests."""

    def reply(model: str, msgs: Sequence[Message]) -> str:
        task = msgs[-1]["content"]
        name = re.search(r"`(total|in_range)`", task)
        if not name or "lost its body" not in task:
            return "I'm not sure."
        if how == "cheat":
            return ("Done.\n\ntest_shop.py\n<<<<<<< SEARCH\n" + TESTS + "=======\n"
                    "def test_nothing():\n    pass\n>>>>>>> REPLACE\n")  # fmt: skip
        body = BODY[name.group(1)] if how == "right" else "    return 0\n"
        search = "    raise NotImplementedError\n"
        return f"Done.\n\nshop.py\n<<<<<<< SEARCH\n{search}=======\n{body}>>>>>>> REPLACE\n"

    return reply


def coach(settings: Settings, shop: Path, coder: str, deep: str = "") -> tuple[Practice, list[str]]:
    scripts = {"pablo/coder": fixer(coder), "pablo/judge": lambda m, x: JUDGE}
    if deep:
        scripts["pablo/deep"] = fixer(deep)
    orch = Orchestrator(settings, FakeBackend(scripts=scripts), repo=shop)
    log: list[str] = []
    p = Practice(orch, teacher="deep" if deep else None, exclude=set(),
                 on_result=lambda r: log.append(f"{r.target}:{r.passed}:{r.lane}"))  # fmt: skip
    return p, log


def test_targets_are_tested_public_functions(shop: Path) -> None:
    names = {t.qualname for t in find_targets(shop, set())}
    assert names == {"total", "in_range"}
    assert {t.qualname for t in find_targets(shop, {"total"})} == {"in_range"}  # suite names skip
    t = next(t for t in find_targets(shop, set()) if t.name == "total")
    assert t.tests == ["test_shop.py"] and t.doc == "Sum of all prices."
    gone = break_function(SHOP, t, "reimplement", random.Random(0))[0]
    assert "raise NotImplementedError" in gone and '"""Sum of all prices."""' in gone
    assert "s += p" not in gone.split("def in_range")[0]
    bugs = break_function(SHOP, next(t for t in find_targets(shop, set()) if t.name == "in_range"),
                          "bugfix", random.Random(1))  # fmt: skip
    assert bugs and all(b != SHOP for b in bugs)


def test_a_right_fix_is_kept_as_an_example(settings: Settings, shop: Path) -> None:
    p, log = coach(settings, shop, "right")
    results = p.session(shop, n=2, kinds=("reimplement",))
    assert sorted(log) == ["shop.py:in_range:True:coder", "shop.py:total:True:coder"]
    assert len(results) == 2 and all(r.passed and r.lane == "coder" for r in results)
    lessons = Lessons(settings.home)
    assert len(lessons.examples()) == 2 and {e.source for e in lessons.examples()} == {"practice"}
    assert lessons.lane_record("shop", "reimplement")["coder"] == (2, 2)
    assert (shop / "shop.py").read_text() == SHOP  # your repo is never touched


def test_editing_the_tests_is_never_kept(settings: Settings, shop: Path) -> None:
    p, _ = coach(settings, shop, "cheat")
    results = p.session(shop, n=1, kinds=("reimplement",))
    assert results and not results[0].passed and "edits tests" in results[0].reason
    assert Lessons(settings.home).examples() == []
    assert CHEAT.search("+++ b/tests/conftest.py") and not CHEAT.search("+++ b/shop.py")


def test_deep_teaches_what_the_coder_missed(settings: Settings, shop: Path) -> None:
    p, _ = coach(settings, shop, "wrong", deep="right")
    results = p.session(shop, n=1, kinds=("reimplement",))
    assert results[0].passed and results[0].lane == "deep" and results[0].tried == ["coder", "deep"]
    lessons = Lessons(settings.home)
    assert [e.lane for e in lessons.examples()] == ["deep"]
    assert lessons.failures() and lessons.failures()[0].lane == "coder"


def test_practice_refuses_the_eval_suite(settings: Settings, shop: Path) -> None:
    from pablo.evals import SUITE

    p, _ = coach(settings, shop, "right")
    with pytest.raises(ValueError, match="eval suite"):
        p.session(SUITE / "repos" / "ledger", n=1)


def test_a_bugfix_task_quotes_the_failing_assertion(settings: Settings, shop: Path) -> None:
    p, _ = coach(settings, shop, "right")
    results = p.session(shop, n=1, kinds=("bugfix",))
    assert results and "fail" in results[0].task and "without changing the tests" in results[0].task
