"""Repo map: symbol extract, BM25 rank, cache, and the quantity→qty synonym."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from pablo.context import pick_files
from pablo.repomap import RepoMap, Symbol, build_map, expand_query, extract_symbols, load_or_build


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "inv"
    (root / "inventory").mkdir(parents=True)
    (root / "inventory" / "__init__.py").write_text("")
    (root / "inventory" / "store.py").write_text(
        '"""Stock on hand."""\n\n'
        "def remove(sku: str, qty: int) -> None:\n"
        '    """Drop qty units of sku."""\n'
        "    pass\n\n"
        "def find(name: str) -> str | None:\n"
        "    return name\n"
    )
    (root / "inventory" / "pricing.py").write_text(
        "def discount(price: int, pct: int) -> int:\n    return price\n"
    )
    (root / "readme.md").write_text("inventory app\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )
    return root


def test_extract_symbols_reads_defs_and_docstrings() -> None:
    src = (
        '"""mod"""\n\n'
        "def remove(sku: str, qty: int) -> None:\n"
        '    """Drop qty units."""\n'
        "    pass\n\n"
        "class Store:\n"
        '    """On disk."""\n\n'
        "    def add(self, sku: str) -> None:\n"
        "        pass\n"
    )
    syms = extract_symbols("inventory/store.py", src)
    names = {s.name for s in syms}
    assert {"remove", "Store", "add"} <= names
    remove = next(s for s in syms if s.name == "remove")
    assert "qty" in remove.signature and "Drop qty" in remove.docstring


def test_ast_fallback_when_tree_sitter_missing() -> None:
    src = "def memory_bytes(spec: str) -> int:\n    return 1\n"
    with patch("pablo.repomap._symbols_via_tree_sitter", return_value=None):
        syms = extract_symbols("sandbox.py", src)
    assert syms[0].name == "memory_bytes"


def test_quantity_synonym_ranks_qty_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rmap = build_map(repo)
    ranked = rmap.rank_files("Remove too many: quantity must not go negative")
    assert ranked[0] == "inventory/store.py"
    assert "qty" in expand_query(["quantity"])


def test_pick_files_uses_map_after_named(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    listed = ["inventory/store.py", "inventory/pricing.py", "inventory/__init__.py"]
    rmap = RepoMap(
        symbols=[
            Symbol("inventory/store.py", "remove", "function", "def remove(sku, qty)", "Drop qty"),
            Symbol("inventory/pricing.py", "discount", "function", "def discount(price, pct)"),
        ]
    )
    picked = pick_files(
        listed,
        "quantity must not go negative when removing stock",
        repo,
        repo_map=rmap,
    )
    assert picked[0] == "inventory/store.py"


def test_cache_round_trip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    first = load_or_build(repo, home=home)
    assert first.symbols
    second = load_or_build(repo, home=home)
    assert [s.name for s in second.symbols] == [s.name for s in first.symbols]
    assert (home / "index" / first.key / "map.json").is_file()
