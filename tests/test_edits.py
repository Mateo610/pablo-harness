from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pablo.edits import (
    EditBlock,
    EditError,
    apply_block,
    blocks_to_diff,
    parse_blocks,
    strip_blocks,
)
from pablo.verify import apply_diff

REPLY = """I added sub and a test.

calc.py
```python
<<<<<<< SEARCH
def add(a, b):
    return a + b
=======
def add(a, b):
    return a + b


def sub(a, b):
    return a - b
>>>>>>> REPLACE
```

test_calc.py
<<<<<<< SEARCH
from calc import add
=======
from calc import add, sub
>>>>>>> REPLACE

`test_calc.py`
<<<<<<< SEARCH
def test_add():
    assert add(2, 3) == 5
=======
def test_add():
    assert add(2, 3) == 5


def test_sub():
    assert sub(5, 3) == 2
>>>>>>> REPLACE
"""


def test_parse_blocks_and_explanation() -> None:
    blocks = parse_blocks(REPLY)
    assert [b.path for b in blocks] == ["calc.py", "test_calc.py", "test_calc.py"]
    assert strip_blocks(REPLY) == "I added sub and a test."


def test_blocks_become_a_diff_git_applies(repo: Path) -> None:
    diff = blocks_to_diff(repo, parse_blocks(REPLY))
    assert diff.startswith("diff --git a/calc.py b/calc.py")
    ok, err = apply_diff(repo, diff, check_only=True)
    assert ok, err
    assert "def sub" not in (repo / "calc.py").read_text()  # repo untouched


def test_new_file_from_empty_search(repo: Path) -> None:
    diff = blocks_to_diff(repo, [EditBlock("pkg/new.py", "", "X = 1\n")])
    assert "new file mode" in diff and "+X = 1" in diff
    assert apply_diff(repo, diff, check_only=True)[0]


def test_tolerates_trailing_whitespace_and_indentation() -> None:
    content = "class A:\n    def f(self):\n        return 1\n"
    # Model dropped the class indentation in SEARCH and REPLACE.
    block = EditBlock("a.py", "def f(self):\n    return 1\n", "def f(self):\n    return 2\n")
    assert apply_block(content, block) == "class A:\n    def f(self):\n        return 2\n"
    trailing = EditBlock("a.py", "    def f(self):   \n", "    def g(self):\n")
    assert "def g(self)" in apply_block(content, trailing)


def test_errors_are_specific_enough_to_repair() -> None:
    content = "x = 1\ny = 2\nx = 1\n"
    with pytest.raises(EditError, match="matches 2 places"):
        apply_block(content, EditBlock("a.py", "x = 1\n", "x = 3\n"))
    with pytest.raises(EditError, match="closest lines in the file: y = 2"):
        apply_block(content, EditBlock("a.py", "y = 3\n", "y = 4\n"))


def test_refuses_paths_outside_repo(repo: Path) -> None:
    for bad in ["../evil.py", "/etc/passwd", "~/.bashrc"]:
        with pytest.raises(EditError, match="outside the repo"):
            blocks_to_diff(repo, [EditBlock(bad, "", "x\n")])


def test_file_without_trailing_newline(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    diff = blocks_to_diff(tmp_path, [EditBlock("f.txt", "b", "c")])
    assert "\\ No newline at end of file" in diff
    assert apply_diff(tmp_path, diff, check_only=True)[0]
