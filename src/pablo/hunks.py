"""Split a unified diff into hunks and rebuild a patch from the ones you keep.

Hunk ids are "<file index>:<hunk index>", stable for a given diff. `git apply`
locates hunks by their original ("-") line numbers, so dropping earlier hunks
from a file doesn't break the later ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Hunk:
    id: str
    header: str
    lines: list[str]

    @property
    def added(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def removed(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("-"))


@dataclass
class FilePatch:
    path: str
    head: list[str]
    hunks: list[Hunk] = field(default_factory=list)


def parse(diff: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git ") or (
            line.startswith("--- ") and (current is None or current.hunks)
        ):
            current = FilePatch(path="", head=[line])
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if line.startswith("@@"):
            hunk = Hunk(id=f"{len(files) - 1}:{len(current.hunks)}", header=line, lines=[])
            current.hunks.append(hunk)
        elif hunk is not None:
            hunk.lines.append(line)
        else:
            current.head.append(line)
            if line.startswith("+++ "):
                path = line[4:].strip()
                current.path = path[2:] if path.startswith("b/") else path
            elif line.startswith("--- ") and not current.path:
                path = line[4:].strip()
                if path != "/dev/null":
                    current.path = path[2:] if path.startswith("a/") else path
    return files


def all_ids(diff: str) -> list[str]:
    return [h.id for f in parse(diff) for h in f.hunks]


def select(diff: str, keep: set[str]) -> str:
    """A patch containing only the hunks in `keep`. Unknown ids raise ValueError."""
    files = parse(diff)
    known = {h.id for f in files for h in f.hunks}
    unknown = keep - known
    if unknown:
        raise ValueError(f"unknown hunks: {', '.join(sorted(unknown))}")
    out: list[str] = []
    for f in files:
        chosen = [h for h in f.hunks if h.id in keep]
        if not chosen:
            continue
        out.extend(f.head)
        for h in chosen:
            out.append(h.header)
            out.extend(h.lines)
    return "\n".join(out) + "\n" if out else ""
