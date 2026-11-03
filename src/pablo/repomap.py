"""Repo symbol map: what each file defines, ranked for a task.

Built with tree-sitter when the grammar is installed, otherwise with the stdlib
`ast` module for Python. Cached under `~/.pablo/index/<repo hash>`. The map feeds
the coder prompt's `tree` section and reorders `pick_files` after named/defined
hits. Serena (optional, later) is a separate process and is not imported here.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pablo.config import default_home
from pablo.context import NOT_SOURCE_DIRS, READ_SUFFIXES, list_files

# Small, one-place abbreviation list: a task word expands to code tokens and back.
SYNONYMS: dict[str, frozenset[str]] = {
    "quantity": frozenset({"qty", "quantities", "amount", "count"}),
    "qty": frozenset({"quantity", "quantities", "amount"}),
    "memory": frozenset({"mem", "ram", "bytes"}),
    "mem": frozenset({"memory", "ram"}),
    "config": frozenset({"cfg", "settings", "conf"}),
    "cfg": frozenset({"config", "settings"}),
    "directory": frozenset({"dir", "folder", "path"}),
    "dir": frozenset({"directory", "folder"}),
    "message": frozenset({"msg"}),
    "msg": frozenset({"message"}),
    "parameter": frozenset({"param", "arg", "argument"}),
    "param": frozenset({"parameter", "arg", "argument"}),
    "function": frozenset({"func", "fn", "method"}),
    "func": frozenset({"function", "fn", "method"}),
    "string": frozenset({"str", "text"}),
    "str": frozenset({"string", "text"}),
    "boolean": frozenset({"bool", "flag"}),
    "bool": frozenset({"boolean", "flag"}),
    "initialize": frozenset({"init", "setup"}),
    "init": frozenset({"initialize", "setup", "__init__"}),
    "remove": frozenset({"delete", "drop", "rm"}),
    "delete": frozenset({"remove", "drop", "rm"}),
    "error": frozenset({"err", "exception", "exc"}),
    "exception": frozenset({"error", "exc", "err"}),
}

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


@dataclass(frozen=True)
class Symbol:
    path: str
    name: str
    kind: str  # function | class | method
    signature: str
    docstring: str = ""
    line: int = 1

    def render(self) -> str:
        doc = f"  # {self.docstring}" if self.docstring else ""
        return f"{self.path}:{self.line} {self.signature}{doc}"


@dataclass
class RepoMap:
    """Symbols for one repo snapshot, keyed for ranking and for the prompt tree."""

    symbols: list[Symbol]
    key: str = ""

    def by_path(self) -> dict[str, list[Symbol]]:
        out: dict[str, list[Symbol]] = {}
        for sym in self.symbols:
            out.setdefault(sym.path, []).append(sym)
        return out

    def render(self, limit: int = 400) -> str:
        """Compact tree for the prompt: one line per symbol, files grouped."""
        lines: list[str] = []
        for path, syms in sorted(self.by_path().items()):
            lines.append(path)
            for sym in syms:
                extra = f"  # {sym.docstring}" if sym.docstring else ""
                lines.append(f"  {sym.signature}{extra}")
                if len(lines) >= limit:
                    return "\n".join(lines) + f"\n… ({len(self.symbols)} symbols total)"
        return "\n".join(lines)

    def rank_files(self, task: str, candidates: Sequence[str] | None = None) -> list[str]:
        """BM25 over symbol names, split identifiers, docstrings, and paths."""
        allowed = set(candidates) if candidates is not None else None
        docs = {
            path: _doc_tokens(path, syms)
            for path, syms in self.by_path().items()
            if allowed is None or path in allowed
        }
        if not docs:
            return list(candidates or [])
        query = expand_query(_query_tokens(task))
        scores = bm25(query, docs)
        ranked = sorted(docs, key=lambda p: (-scores.get(p, 0.0), p))
        if candidates is not None:
            missing = [p for p in candidates if p not in docs]
            return [*ranked, *missing]
        return ranked

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "symbols": [asdict(s) for s in self.symbols]}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RepoMap:
        return cls(
            key=str(data.get("key", "")),
            symbols=[Symbol(**row) for row in data.get("symbols", [])],
        )


def split_ident(name: str) -> list[str]:
    """`format_cents` / `HTTPServer` → lowercase pieces for matching task words."""
    parts = name.replace("-", "_").split("_")
    out: list[str] = []
    for part in parts:
        out.extend(p.lower() for p in _CAMEL.findall(part) if p)
    return out or ([name.lower()] if name else [])


def expand_query(tokens: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        low = tok.lower()
        for piece in (low, *split_ident(tok), *SYNONYMS.get(low, ())):
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    return out


def _query_tokens(task: str) -> list[str]:
    return [m.group(0) for m in _TOKEN.finditer(task)]


def _doc_tokens(path: str, syms: Sequence[Symbol]) -> list[str]:
    bag: list[str] = []
    for piece in Path(path).parts:
        bag.extend(split_ident(Path(piece).stem if "." in piece else piece))
    for sym in syms:
        bag.extend(split_ident(sym.name))
        bag.extend(t.lower() for t in _TOKEN.findall(sym.signature))
        bag.extend(t.lower() for t in _TOKEN.findall(sym.docstring))
    return bag


def bm25(
    query: Sequence[str],
    docs: dict[str, list[str]],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    """Classic BM25 over already-tokenized documents."""
    if not docs or not query:
        return {path: 0.0 for path in docs}
    n = len(docs)
    avgdl = sum(len(toks) for toks in docs.values()) / n
    df: dict[str, int] = {}
    for toks in docs.values():
        for term in set(toks):
            df[term] = df.get(term, 0) + 1
    scores = {path: 0.0 for path in docs}
    for term in query:
        n_q = df.get(term, 0)
        if n_q == 0:
            continue
        idf = math.log(1 + (n - n_q + 0.5) / (n_q + 0.5))
        for path, toks in docs.items():
            tf = toks.count(term)
            if tf == 0:
                continue
            dl = len(toks) or 1
            scores[path] += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
    return scores


def first_docline(text: str | None) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        clean = line.strip().strip("\"'")
        if clean:
            return clean[:120]
    return ""


def _symbols_via_ast(path: str, source: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[Symbol] = []

    def add(node: ast.AST, kind: str, name: str, signature: str) -> None:
        doc = ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = first_docline(ast.get_docstring(node))
        out.append(
            Symbol(
                path=path,
                name=name,
                kind=kind,
                signature=signature,
                docstring=doc,
                line=getattr(node, "lineno", 1),
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ast.unparse(node.args) if hasattr(ast, "unparse") else "..."
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            add(node, "function", node.name, f"{prefix} {node.name}({args})")
        elif isinstance(node, ast.ClassDef):
            add(node, "class", node.name, f"class {node.name}")
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = ast.unparse(child.args) if hasattr(ast, "unparse") else "..."
                    prefix = "async def" if isinstance(child, ast.AsyncFunctionDef) else "def"
                    add(child, "method", child.name, f"{prefix} {node.name}.{child.name}({args})")
    return out


def _symbols_via_tree_sitter(path: str, source: bytes) -> list[Symbol] | None:
    """Return symbols, or None if tree-sitter / the Python grammar is unavailable."""
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser
    except ImportError:
        return None
    language = Language(tspython.language())
    parser = Parser(language)
    tree = parser.parse(source)
    out: list[Symbol] = []
    text = source

    def node_text(node: Any) -> str:
        return text[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def docstring_after(node: Any) -> str:
        # First statement in the body if it is a string expression.
        for child in node.children:
            if child.type == "block":
                for stmt in child.children:
                    if stmt.type == "expression_statement" and stmt.child_count:
                        inner = stmt.children[0]
                        if inner.type == "string":
                            return first_docline(node_text(inner))
                    break
        return ""

    def walk(node: Any, class_name: str = "") -> None:
        if node.type in {"function_definition", "async_function_definition"}:
            name_node = node.child_by_field_name("name")
            params = node.child_by_field_name("parameters")
            if name_node is not None:
                name = node_text(name_node)
                args = node_text(params) if params is not None else "()"
                prefix = "async def" if node.type.startswith("async") else "def"
                if class_name:
                    sig = f"{prefix} {class_name}.{name}{args}"
                    kind = "method"
                else:
                    sig = f"{prefix} {name}{args}"
                    kind = "function"
                out.append(
                    Symbol(
                        path=path,
                        name=name,
                        kind=kind,
                        signature=sig,
                        docstring=docstring_after(node),
                        line=node.start_point[0] + 1,
                    )
                )
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            cname = node_text(name_node) if name_node is not None else ""
            out.append(
                Symbol(
                    path=path,
                    name=cname,
                    kind="class",
                    signature=f"class {cname}",
                    docstring=docstring_after(node),
                    line=node.start_point[0] + 1,
                )
            )
            for child in node.children:
                walk(child, cname)
            return
        for child in node.children:
            walk(child, class_name)

    walk(tree.root_node)
    return out


def extract_symbols(path: str, source: str) -> list[Symbol]:
    if path.endswith(".py"):
        via = _symbols_via_tree_sitter(path, source.encode("utf-8"))
        if via is not None:
            return via
        return _symbols_via_ast(path, source)
    return []


def repo_key(repo: Path, listed: Sequence[str] | None = None) -> str:
    """Stable id for the cache: content hash of tracked source files."""
    files = list(listed) if listed is not None else list_files(repo)
    digest = hashlib.sha256()
    try:
        head = subprocess_head(repo)
    except OSError:
        head = ""
    digest.update(head.encode() + b"\0")
    for rel in files:
        if not rel.endswith(READ_SUFFIXES):
            continue
        path = repo / rel
        digest.update(rel.encode() + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return digest.hexdigest()[:24]


def subprocess_head(repo: Path) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def index_dir(home: Path | None = None) -> Path:
    return (home or default_home()) / "index"


def cache_path(key: str, home: Path | None = None) -> Path:
    return index_dir(home) / key / "map.json"


def build_map(repo: Path, listed: Sequence[str] | None = None) -> RepoMap:
    files = list(listed) if listed is not None else list_files(repo)
    symbols: list[Symbol] = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        if NOT_SOURCE_DIRS.intersection(Path(rel).parts):
            continue
        try:
            source = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        symbols.extend(extract_symbols(rel, source))
    return RepoMap(symbols=symbols, key=repo_key(repo, files))


def load_or_build(
    repo: Path, home: Path | None = None, listed: Sequence[str] | None = None
) -> RepoMap:
    files = list(listed) if listed is not None else list_files(repo)
    key = repo_key(repo, files)
    path = cache_path(key, home)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cached = RepoMap.from_json(data)
            if cached.key == key:
                return cached
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    built = build_map(repo, files)
    built.key = key
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(built.to_json()), encoding="utf-8")
    except OSError:
        pass
    return built
