from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pablo.context import (
    Section,
    assemble,
    build_coder_context,
    clip,
    load_skills,
    pick_skill,
    tokens,
)
from pablo.verify import run_check


def test_clip_keeps_whole_lines_and_says_so() -> None:
    text = "\n".join(f"line {i}" for i in range(500))
    out = clip(text, 100)
    assert tokens(out) <= 100
    assert out.splitlines()[0] == "line 0" and "more lines cut to fit" in out


def test_assemble_shrinks_files_first() -> None:
    big = "x = 1\n" * 20_000
    out = assemble([Section("task", "do the thing", 1000), Section("files", big, 8000)], total=2000)
    assert out.startswith("do the thing") and tokens(out) <= 2100


def test_every_skill_card_is_small_and_loads() -> None:
    skills = load_skills()
    assert {s.name for s in skills} >= {"testing", "fastapi", "debugging", "react"}
    assert all(tokens(s.body) <= 300 for s in skills)


def test_pick_one_skill() -> None:
    assert pick_skill("add a FastAPI endpoint GET /health").name == "fastapi"  # type: ignore[union-attr]
    assert pick_skill("write pytest tests for slugify").name == "testing"  # type: ignore[union-attr]
    assert pick_skill("the parser crashes on empty input").name == "debugging"  # type: ignore[union-attr]
    assert pick_skill("make the sky blue") is None


def test_coder_context_fits_with_a_huge_file(tmp_path: Path) -> None:
    (tmp_path / "small.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "huge.py").write_text("\n".join(f"v{i} = {i}" for i in range(50_000)))
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    out = build_coder_context(tmp_path, "fix the bug in small.py where add fails")
    assert "Guidance:" in out and "Reproduce first" in out  # debugging card
    assert "   1|def add(a, b):" in out
    assert tokens(out) <= 10_600


def test_no_tests_collected_is_a_failure(tmp_path: Path) -> None:
    """pytest exits 5 when nothing is collected; the old harness reported that as 0/0 = fine."""
    (tmp_path / "mod.py").write_text("x = 1\n")
    check = run_check(
        "tests", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tmp_path
    )
    assert check.exit_code == 5 and not check.passed
