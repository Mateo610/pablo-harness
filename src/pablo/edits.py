"""Find-and-replace edit blocks: the coder says what to change, git writes the diff.

Local models are bad at unified diffs (hunk counts, context lines, phantom blanks; see M1 in
docs/benchmarks.md) and good at copying a few lines and rewriting them. So the coder replies with

    path/to/file.py
    <<<<<<< SEARCH
    exact lines from the file
    =======
    the new lines
    >>>>>>> REPLACE

and the harness applies the blocks to a throwaway copy of the touched files and asks `git diff`
for the patch. Everything downstream (checks, judge, hunks, approve) still sees a normal diff.
An empty SEARCH creates a new file. Matching is exact first, then forgiving about trailing
whitespace, then about indentation (the replacement is re-indented to match the file).
"""

from __future__ import annotations

import difflib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

BLOCK_RE = re.compile(
    r"^(?P<path>[^\n]*)\n(?:```[\w-]*\n)?<{5,9} SEARCH\n(?P<search>.*?)^={5,9}\n"
    r"(?P<replace>.*?)^>{5,9} REPLACE[^\n]*$",
    re.S | re.M,
)


@dataclass(frozen=True)
class EditBlock:
    path: str
    search: str
    replace: str


class EditError(ValueError):
    pass


def _clean_path(raw: str) -> str:
    path = raw.strip().strip("`*").strip()
    path = re.sub(r"^(?:#+\s*|file:\s*|path:\s*)", "", path, flags=re.I).strip("`* ")
    return path


def parse_blocks(text: str) -> list[EditBlock]:
    blocks = []
    for match in BLOCK_RE.finditer(text):
        path = _clean_path(match.group("path"))
        blocks.append(EditBlock(path, match.group("search"), match.group("replace")))
    return blocks


def has_blocks(text: str) -> bool:
    return bool(re.search(r"^<{5,9} SEARCH$", text, re.M))


def strip_blocks(text: str) -> str:
    """The explanation: everything outside the edit blocks and their fences."""
    out = BLOCK_RE.sub("", text)
    out = re.sub(r"^```[\w-]*[ \t]*$", "", out, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _safe_path(repo: Path, rel: str) -> Path:
    if not rel or rel.startswith(("/", "~")) or ".." in Path(rel).parts:
        raise EditError(f"refusing path outside the repo: {rel!r}")
    target = (repo / rel).resolve()
    if repo.resolve() not in target.parents:
        raise EditError(f"refusing path outside the repo: {rel!r}")
    return target


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _find_lines(content: str, search: str) -> tuple[int, int, str] | None:
    """Whitespace-tolerant match. Returns (start, end) line indexes and the indent delta mode."""
    file_lines = content.splitlines()
    want = search.splitlines()
    while want and not want[-1].strip():
        want.pop()
    if not want:
        return None
    n = len(want)
    for mode, norm in (("rstrip", str.rstrip), ("strip", str.strip)):
        hits = [
            i
            for i in range(len(file_lines) - n + 1)
            if all(norm(file_lines[i + j]) == norm(want[j]) for j in range(n))
        ]
        if len(hits) == 1:
            return hits[0], hits[0] + n, mode
        if len(hits) > 1:
            raise EditError(
                f"SEARCH matches {len(hits)} places; include more lines to make it unique"
            )
    return None


def apply_block(content: str, block: EditBlock) -> str:
    if block.search in content and block.search:
        count = content.count(block.search)
        if count > 1:
            raise EditError(
                f"{block.path}: SEARCH matches {count} places; include more lines to make it unique"
            )
        return content.replace(block.search, block.replace, 1)
    found = _find_lines(content, block.search)
    if found is None:
        first = next((ln for ln in block.search.splitlines() if ln.strip()), "")
        close = difflib.get_close_matches(first, content.splitlines(), n=3, cutoff=0.5)
        hint = (
            ("; closest lines in the file: " + " | ".join(c.strip() for c in close))
            if close
            else ""
        )
        raise EditError(
            f"{block.path}: SEARCH text not found (first line: {first.strip()!r}){hint}"
        )
    start, end, mode = found
    lines = content.splitlines(keepends=True)
    replace = block.replace.splitlines(keepends=True)
    if mode == "strip" and replace:
        # Re-indent the replacement by the difference between the file and the SEARCH text.
        file_first = next(ln for ln in lines[start:end] if ln.strip())
        search_first = next(ln for ln in block.search.splitlines() if ln.strip())
        have, want = _indent(file_first), _indent(search_first)
        fixed = []
        for ln in replace:
            if ln.strip() and ln.startswith(want):
                ln = have + ln[len(want) :]
            elif ln.strip() and not want:
                ln = have + ln
            fixed.append(ln)
        replace = fixed
    if replace and not replace[-1].endswith("\n") and end < len(lines):
        replace[-1] += "\n"
    return "".join(lines[:start] + replace + lines[end:])


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def blocks_to_diff(repo: Path, blocks: list[EditBlock]) -> str:
    """Apply blocks to copies of the touched files and return git's diff. Raises EditError."""
    if not blocks:
        raise EditError("no edit blocks found")
    new: dict[str, str] = {}
    old: dict[str, str | None] = {}
    for block in blocks:
        target = _safe_path(repo, block.path)
        if block.path not in new:
            old[block.path] = target.read_text("utf-8") if target.is_file() else None
            new[block.path] = old[block.path] or ""
        if old[block.path] is None and not block.search.strip() and not new[block.path]:
            new[block.path] = block.replace
            continue
        if not block.search.strip():
            raise EditError(f"{block.path}: empty SEARCH on a file that already has content")
        new[block.path] = apply_block(new[block.path], block)

    with tempfile.TemporaryDirectory(prefix="pablo-edits-") as tmp:
        work = Path(tmp)
        _git(work, "init", "-q")
        for rel, text in old.items():
            if text is not None:
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                (work / rel).write_text(text, "utf-8")
        _git(work, "add", "-A")
        _git(
            work,
            "-c",
            "user.name=pablo",
            "-c",
            "user.email=pablo@localhost",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "base",
        )
        for rel, text in new.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text, "utf-8")
        _git(work, "add", "-A", "--intent-to-add")
        diff = _git(work, "diff")
    if not diff.strip():
        raise EditError("the edit blocks did not change anything")
    return diff
