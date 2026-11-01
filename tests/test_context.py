from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pablo.context import (
    MAX_FILES,
    Budget,
    Section,
    assemble,
    build_coder_context,
    clip,
    list_files,
    load_skills,
    pick_files,
    pick_skill,
    task_refs,
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


def _ledger(tmp_path: Path) -> Path:
    """A small package where the task names functions, never files (like the M4b bake-off)."""
    root = tmp_path / "ledger-repo"
    (root / "ledger").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "ledger" / "__init__.py").write_text("")
    (root / "ledger" / "money.py").write_text(
        "def format_cents(cents: int) -> str:\n    return f'${cents / 100:.2f}'\n"
    )
    (root / "ledger" / "entries.py").write_text(
        "def load_line(line):\n    day, payee, dollars = line.split(',')\n    return day, payee\n\n"
        "def in_month(entry, month):\n    return entry[0][5:7] == month\n"
    )
    (root / "ledger" / "report.py").write_text(
        "from ledger.money import format_cents\n\n\ndef monthly(entries, month):\n"
        "    return format_cents(sum(e[2] for e in entries) // 100)\n"
    )
    (root / "tests" / "test_ledger.py").write_text(
        "from ledger.money import format_cents\n\n\ndef test_fmt():\n"
        "    assert format_cents(150) == '$1.50'\n"
    )
    for i in range(8):
        (root / "ledger" / f"aaa_unrelated_{i}.py").write_text(f"X{i} = {i}\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root


def test_task_refs_reads_symbols_and_modules() -> None:
    refs = task_refs("entries.in_month and report.monthly both slice strings; see `total`.")
    assert {"entries", "report"} <= refs.stems
    assert {"in_month", "monthly", "total"} <= refs.symbols
    assert "money" not in refs.stems  # a file name, matched by path instead
    assert not task_refs("Fix it. Then ship ledger/money.py.").symbols


def test_pick_files_finds_the_definition_the_task_never_names(tmp_path: Path) -> None:
    repo = _ledger(tmp_path)
    listed = list_files(repo)
    picked = pick_files(listed, "load_line splits on every comma; fix it", repo)
    assert picked[0] == "ledger/entries.py"
    picked = pick_files(listed, "report.monthly prints an amount 100 times too small", repo)
    assert picked[:1] == ["ledger/report.py"]
    # Users of the named symbol come next: the test that imports format_cents.
    picked = pick_files(listed, "Move format_cents into ledger/format.py", repo)
    assert picked[:3] == ["ledger/money.py", "ledger/report.py", "tests/test_ledger.py"]
    # Without a repo only paths count (the old behavior).
    assert pick_files(listed, "load_line is broken")[0] == "ledger/__init__.py"


def test_budget_grows_with_the_lane_window(tmp_path: Path) -> None:
    assert Budget.for_window(10_500) == Budget()
    big = Budget.for_window(24_000)
    assert big.total == 24_000 and big.files > Budget().files and big.max_files > MAX_FILES
    assert big.files + big.tree + 2_000 <= big.total + 2_000
    repo = _ledger(tmp_path)
    small = build_coder_context(repo, "load_line splits on every comma")
    whole = build_coder_context(repo, "load_line splits on every comma", budget=big)
    assert "--- ledger/entries.py" in small
    assert whole.count("\n--- ") > small.count("\n--- ")
    assert tokens(whole) <= 24_100


def test_one_huge_file_cannot_crowd_out_the_rest(tmp_path: Path) -> None:
    (tmp_path / "big.py").write_text("\n".join(f"def f{i}(): pass" for i in range(20_000)))
    (tmp_path / "small.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    out = build_coder_context(tmp_path, "big.py and small.py both need a docstring")
    assert "--- small.py" in out and "def add(a, b):" in out


def test_a_file_quoting_the_task_is_not_context(tmp_path: Path) -> None:
    task = "Explain how a check is limited by memory and processes on this machine"
    (tmp_path / "tasks.yaml").write_text(f"- task: {task}\n")
    (tmp_path / "limits.py").write_text("MEMORY = '4g'  # check memory limit, processes 256\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    assert pick_files(list_files(tmp_path), task, tmp_path) == ["limits.py"]


def test_a_repo_outside_git_skips_environments_and_caches(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def load_line(line):\n    return line\n")
    for junk in (".venv/lib/python3.11/site-packages/x.py", "node_modules/y/index.js",
                 "__pycache__/app.cpython-311.pyc", ".pablo/plan.yaml"):  # fmt: skip
        (tmp_path / junk).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / junk).write_text("load_line = 1\n")
    assert list_files(tmp_path) == ["app.py"]
