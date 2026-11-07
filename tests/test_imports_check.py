"""The `imports` check: each module imported on its own catches order-dependent imports."""

from __future__ import annotations

import sys
from pathlib import Path

from pablo.verify import IMPORTS_CHECK, load_checks, run_check

CHECK = [sys.executable, *IMPORTS_CHECK[1:]]


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_circular_import_from_a_move_fails(tmp_path: Path) -> None:
    """The M7 false ready: pricing re-exports bulk, bulk imports pricing at the top."""
    repo = write(
        tmp_path,
        {
            "inventory/__init__.py": "",
            "inventory/pricing.py": "def discount(p, pct):\n    return p - p * pct // 100\n\n\n"
            "from inventory.bulk import bulk_price  # noqa\n",
            "inventory/bulk.py": "from inventory.pricing import discount\n\n\n"
            "def bulk_price(p, q):\n    return discount(p, 10) * q\n",
            "tests/test_x.py": "import inventory.pricing\n",  # the lucky order: never checked
        },
    )
    check = run_check("imports", CHECK, repo)
    assert not check.passed
    assert "FAILED import inventory.bulk" in check.output_tail
    assert "Found 1 errors" in check.output_tail


def test_clean_repo_passes_and_counts_modules(tmp_path: Path) -> None:
    repo = write(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "from pkg.b import B\n",
            "src/pkg/b.py": "B = 1\n",
            "tool.py": "X = 1\n",
            "test_tool.py": "raise SystemExit('tests are not imported')\n",
            "conftest.py": "raise SystemExit('nor is conftest')\n",
            "scripts/run.py": "raise SystemExit('nor scripts')\n",
        },
    )
    check = run_check("imports", CHECK, repo)
    assert check.passed, check.output_tail
    assert "imported 4 of 4 modules" in check.output_tail


def test_python_repos_get_the_check_unless_turned_off(tmp_path: Path) -> None:
    write(tmp_path, {"app.py": "X = 1\n", ".pablo/checks.yaml": "tests: python -m pytest -q\n"})
    assert list(load_checks(tmp_path)) == ["tests", "imports"]
    (tmp_path / ".pablo/checks.yaml").write_text("tests: python -m pytest -q\nimports: off\n")
    assert list(load_checks(tmp_path)) == ["tests"]
    assert load_checks(tmp_path / ".pablo") == {}
