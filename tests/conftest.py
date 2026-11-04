from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pablo.config import SandboxConfig, Settings, load_settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = load_settings(ROOT / "config" / "lanes.yaml")
    s.home = tmp_path / "home"
    s.sandbox = SandboxConfig()  # local: CI has no container engine
    for lane in s.lanes.values():
        lane.retry_when = "off"  # tests that want coder → deep retries turn it on
    return s


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tiny Python project under git with one passing test."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    return root
