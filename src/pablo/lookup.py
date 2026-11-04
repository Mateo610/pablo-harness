"""Fetch definitions and references for symbols the coder names.

The harness asks the coder which symbols it needs, then this module returns
definition + reference snippets before the edit call. Lookup runs in a separate
process (`python -m pablo.lookup`) so a future Serena/GPL backend stays behind
the same CLI boundary. The default backend uses the repo map for definitions and
a plain scan for references; tests inject a fake client.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pablo.context import READ_SUFFIXES, list_files
from pablo.repomap import RepoMap, load_or_build

MAX_HITS = 12
MAX_SNIPPET_LINES = 40


@dataclass(frozen=True)
class SymbolHit:
    name: str
    path: str
    kind: str  # definition | reference
    line: int
    snippet: str


@dataclass
class LookupResult:
    symbols: list[str] = field(default_factory=list)
    hits: list[SymbolHit] = field(default_factory=list)
    error: str = ""

    def render(self, limit_chars: int = 6_000) -> str:
        if self.error:
            return f"(lookup failed: {self.error})"
        if not self.hits:
            return "(no definitions or references found for: " + ", ".join(self.symbols) + ")"
        blocks: list[str] = []
        used = 0
        for hit in self.hits:
            block = f"### {hit.kind} {hit.name} · {hit.path}:{hit.line}\n{hit.snippet}"
            if used + len(block) > limit_chars:
                blocks.append("… (lookup clipped to fit the prompt)")
                break
            blocks.append(block)
            used += len(block) + 2
        return "\n\n".join(blocks)

    def to_json(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "hits": [asdict(h) for h in self.hits],
            "error": self.error,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LookupResult:
        return cls(
            symbols=list(data.get("symbols", [])),
            hits=[SymbolHit(**h) for h in data.get("hits", [])],
            error=str(data.get("error", "")),
        )


class LookupClient(Protocol):
    def lookup(
        self, repo: Path, symbols: Sequence[str], home: Path | None = None
    ) -> LookupResult: ...


def parse_symbol_request(text: str) -> list[str] | None:
    """Pull a JSON list of symbol names from the coder's first reply, or None."""
    match = re.search(r"\[[\s\S]*?\]", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    names = [str(x).strip() for x in data if str(x).strip()]
    return names or None


def _snippet(path: Path, line: int, radius: int = 8) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, line - 1 - radius)
    end = min(len(lines), line - 1 + radius + 1)
    numbered = [f"{i + 1:4}|{lines[i]}" for i in range(start, end)]
    return "\n".join(numbered[:MAX_SNIPPET_LINES])


def local_lookup(
    repo: Path, symbols: Sequence[str], home: Path | None = None, rmap: RepoMap | None = None
) -> LookupResult:
    """Definitions from the repo map; references from a word-boundary scan."""
    names = [s for s in symbols if s]
    if not names:
        return LookupResult(error="no symbols requested")
    listed = list_files(repo)
    index = rmap or load_or_build(repo, home=home, listed=listed)
    hits: list[SymbolHit] = []
    seen: set[tuple[str, str, int]] = set()

    def add(hit: SymbolHit) -> None:
        key = (hit.kind, hit.path, hit.line)
        if key in seen or len(hits) >= MAX_HITS:
            return
        seen.add(key)
        hits.append(hit)

    for name in names:
        for sym in index.symbols:
            if sym.name != name and not sym.name.endswith("." + name):
                continue
            add(
                SymbolHit(
                    name=name,
                    path=sym.path,
                    kind="definition",
                    line=sym.line,
                    snippet=_snippet(repo / sym.path, sym.line)
                    or f"{sym.signature}\n# {sym.docstring}".strip(),
                )
            )

    for rel in listed:
        if not rel.endswith(READ_SUFFIXES) or len(hits) >= MAX_HITS:
            continue
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name in names:
                if re.search(rf"\b{re.escape(name)}\b", line):
                    # Skip the definition line we already have.
                    if any(h.path == rel and h.line == i and h.kind == "definition" for h in hits):
                        continue
                    add(
                        SymbolHit(
                            name=name,
                            path=rel,
                            kind="reference",
                            line=i,
                            snippet=_snippet(path, i, radius=4),
                        )
                    )
    return LookupResult(symbols=list(names), hits=hits)


@dataclass
class ProcessLookup:
    """Runs lookup in a child process so GPL backends can share this boundary."""

    python: str = field(default_factory=lambda: sys.executable)

    def lookup(self, repo: Path, symbols: Sequence[str], home: Path | None = None) -> LookupResult:
        payload = {
            "repo": str(repo),
            "symbols": list(symbols),
            "home": str(home) if home else "",
        }
        try:
            proc = subprocess.run(
                [self.python, "-m", "pablo.lookup"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return LookupResult(symbols=list(symbols), error=str(exc))
        if proc.returncode != 0:
            return LookupResult(
                symbols=list(symbols),
                error=(proc.stderr or proc.stdout or f"exit {proc.returncode}")[-400:],
            )
        try:
            return LookupResult.from_json(json.loads(proc.stdout))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return LookupResult(symbols=list(symbols), error=f"bad lookup reply: {exc}")


@dataclass
class InlineLookup:
    """In-process lookup for tests that mock or skip the subprocess."""

    def lookup(self, repo: Path, symbols: Sequence[str], home: Path | None = None) -> LookupResult:
        return local_lookup(repo, symbols, home=home)


def main() -> None:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
        result = local_lookup(
            Path(req["repo"]),
            list(req.get("symbols", [])),
            home=Path(req["home"]) if req.get("home") else None,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result = LookupResult(error=str(exc))
    sys.stdout.write(json.dumps(result.to_json()))


if __name__ == "__main__":
    main()
