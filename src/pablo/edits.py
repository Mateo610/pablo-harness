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
import shutil
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
    if not rel:
        raise EditError(
            "an edit block has no file path: put the repo-relative path alone on the line "
            "before <<<<<<< SEARCH"
        )
    if rel.startswith(("/", "~")) or ".." in Path(rel).parts:
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


def _where_it_diverges(content: str, search: str) -> str:
    """Where a SEARCH that starts right goes wrong: the first line it gets different."""
    want = [ln for ln in search.splitlines()]
    while want and not want[0].strip():
        want.pop(0)
    if not want:
        return ""
    lines = content.splitlines()
    best: tuple[int, int] | None = None  # (matched lines, start index)
    for i, line in enumerate(lines):
        if line.strip() != want[0].strip():
            continue
        n = 0
        while n < len(want) and i + n < len(lines) and lines[i + n].strip() == want[n].strip():
            n += 1
        if best is None or n > best[0]:
            best = (n, i)
    if best is None or best[0] >= len(want):
        return ""
    n, start = best
    have = lines[start + n].strip() if start + n < len(lines) else "(end of file)"
    return (
        f"it matches {n} line(s) from line {start + 1}, then the file has {have!r} "
        f"where SEARCH has {want[n].strip()!r}"
    )


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
        diverges = _where_it_diverges(content, block.search)
        if diverges:
            raise EditError(f"{block.path}: SEARCH text not found; {diverges}")
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


def blocks_on_top(repo: Path, base_diff: str, blocks: list[EditBlock]) -> str:
    """Apply blocks to the repo *after* `base_diff`, and return one diff from the original.

    A coder fixing its own red checks often writes SEARCH text from its edited version of a
    file. This lets those blocks land on that version. Raises EditError.
    """
    if not blocks:
        raise EditError("no edit blocks found")
    with tempfile.TemporaryDirectory(prefix="pablo-stack-") as tmp:
        work = Path(tmp) / "work"
        shutil.copytree(
            repo,
            work,
            ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__"),
        )
        _git(work, "init", "-q")
        _git(work, "add", "-A")
        _git(work, "-c", "user.name=pablo", "-c", "user.email=pablo@localhost",
             "commit", "-q", "--allow-empty", "-m", "base")  # fmt: skip
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=work, input=base_diff, text=True, capture_output=True,
        )  # fmt: skip
        if applied.returncode != 0:
            raise EditError(f"the earlier edits no longer apply: {applied.stderr.strip()}")
        for block in blocks:
            target = _safe_path(work, block.path)
            current = target.read_text("utf-8") if target.is_file() else ""
            if not block.search.strip():
                if current:
                    raise EditError(
                        f"{block.path}: empty SEARCH on a file that already has content"
                    )
                updated = block.replace
            else:
                updated = apply_block(current, block)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(updated, "utf-8")
        _git(work, "add", "-A", "--intent-to-add")
        diff = _git(work, "diff", "HEAD")
    if not diff.strip():
        raise EditError("the edit blocks undid every change")
    return diff
