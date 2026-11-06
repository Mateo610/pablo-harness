"""What Pablo has learned without retraining: examples, past failures, and which lane works.

Three small stores under ~/.pablo/lessons/, all JSONL, all local:

- examples.jsonl  diffs that were right: ones you approved (whole, not partial) and practice
                  fixes that passed the repo's real tests. Shown to the coder as 1 or 2 examples
                  of how changes look in this repo (`learn.examples`).
- failures.jsonl  what went wrong on earlier attempts at similar changes (the first error line
                  of a check that got worse). A hint, never a training row (`learn.failures`).
- outcomes.jsonl  per repo and task type: did the coder or deep get it right? After enough
                  evidence the router may prefer deep for a kind of task (`learn.routing`).

Nothing here comes from Claude, the eval suite, or the legacy data folder.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pablo.context import clip, tokens

Source = Literal["approved", "practice"]
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "should",
        "would",
        "when",
        "make",
        "add",
        "use",
    ]
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def words(text: str) -> list[str]:
    out: list[str] = []
    for w in _WORD.findall(text):
        low = w.lower()
        if low in _STOP:
            continue
        if len(low) > 4 and low.endswith("s") and not low.endswith("ss"):
            low = low[:-1]  # amounts -> amount: plural and singular should meet
        out.append(low)
        out.extend(p for p in low.split("_") if len(p) > 2 and p != low)
    return out


def bm25(query: list[str], docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> list[float]:
    if not docs:
        return []
    avg = sum(len(d) for d in docs) / len(docs) or 1.0
    df = Counter(w for d in docs for w in set(d))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for q in set(query):
            if q not in tf:
                continue
            idf = math.log(1 + (len(docs) - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * tf[q] * (k1 + 1) / (tf[q] + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    return scores


@dataclass
class Example:
    repo: str
    task: str
    diff: str
    lane: str
    source: Source
    kind: str = ""  # task type, or the practice kind (reimplement, bugfix)
    files: list[str] = field(default_factory=list)
    at: str = field(default_factory=_now)


@dataclass
class Failure:
    repo: str
    task: str
    lane: str
    error: str  # the first telling line of the check that got worse
    files: list[str] = field(default_factory=list)
    at: str = field(default_factory=_now)


@dataclass
class Outcome:
    repo: str
    kind: str
    lane: str
    ok: bool
    source: str = "approved"
    at: str = field(default_factory=_now)


class Lessons:
    def __init__(self, home: Path, *, frozen: bool = False) -> None:
        """`frozen`: read only. Evals use it, so one task's lesson can't reach the next."""
        self.dir = home / "lessons"
        self.frozen = frozen

    def _append(self, name: str, row: Any) -> None:
        if self.frozen:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / name).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(row)) + "\n")

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self.dir / name
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # examples -------------------------------------------------------------------------------

    def add_example(self, ex: Example) -> None:
        if ex.diff.strip():
            self._append("examples.jsonl", ex)

    def examples(self) -> list[Example]:
        return [Example(**row) for row in self._read("examples.jsonl")]

    def pick_examples(
        self, repo: str, task: str, *, k: int = 2, max_tokens: int = 1500
    ) -> list[Example]:
        """The k most similar right answers from this repo, within a token budget."""
        pool = [e for e in self.examples() if e.repo == repo]
        if not pool:
            return []
        docs = [words(f"{e.task} {' '.join(e.files)} {e.diff[:2000]}") for e in pool]
        scores = bm25(words(task), docs)
        ranked = sorted(zip(scores, pool, strict=True), key=lambda p: -p[0])
        if ranked and ranked[0][0] <= 0:
            # Nothing similar: one recent example still shows how changes look in this repo.
            ranked = [(1.0, pool[-1])]
        out: list[Example] = []
        used = 0
        for score, ex in ranked:
            if score <= 0 or len(out) >= k:
                break
            cost = tokens(ex.task) + tokens(ex.diff)
            if used + cost > max_tokens:
                continue
            out.append(ex)
            used += cost
        return out

    def examples_section(self, repo: str, task: str, max_tokens: int = 1500) -> str:
        picked = self.pick_examples(repo, task, max_tokens=max_tokens)
        if not picked:
            return ""
        parts = [f"Task: {clip(e.task, 120)}\n```diff\n{e.diff.strip()}\n```" for e in picked]
        return (
            "Changes that were right in this repo before "
            "(match their style, not their content):\n\n" + "\n\n".join(parts)
        )

    # failures -------------------------------------------------------------------------------

    def add_failure(self, f: Failure) -> None:
        if f.error.strip():
            self._append("failures.jsonl", f)

    def failures(self) -> list[Failure]:
        return [Failure(**row) for row in self._read("failures.jsonl")]

    def failures_section(self, repo: str, task: str, k: int = 3) -> str:
        pool = [f for f in self.failures() if f.repo == repo]
        if not pool:
            return ""
        scores = bm25(words(task), [words(f"{f.task} {' '.join(f.files)}") for f in pool])
        seen: list[str] = []
        for score, f in sorted(zip(scores, pool, strict=True), key=lambda p: -p[0]):
            if score <= 0 or len(seen) >= k:
                break
            if f.error not in seen:
                seen.append(f.error)
        if not seen:
            return ""
        return "Earlier attempts at similar changes here broke with:\n" + "\n".join(
            f"- {clip(e, 60)}" for e in seen
        )

    # outcomes -------------------------------------------------------------------------------

    def add_outcome(self, o: Outcome) -> None:
        self._append("outcomes.jsonl", o)

    def lane_record(self, repo: str, kind: str) -> dict[str, tuple[int, int]]:
        """lane -> (right, tried) for this repo and task type."""
        out: dict[str, tuple[int, int]] = {}
        for row in self._read("outcomes.jsonl"):
            if row["repo"] == repo and row["kind"] == kind:
                right, tried = out.get(row["lane"], (0, 0))
                out[row["lane"]] = (right + bool(row["ok"]), tried + 1)
        return out

    def prefer_deep(
        self, repo: str, kind: str, *, min_tries: int = 5, margin: float = 0.25
    ) -> bool:
        """True when deep has clearly beaten the coder on this kind of task in this repo."""
        record = self.lane_record(repo, kind)
        coder, deep = record.get("coder", (0, 0)), record.get("deep", (0, 0))
        if coder[1] < min_tries or deep[1] < min_tries:
            return False
        return deep[0] / deep[1] - coder[0] / coder[1] >= margin


def first_error(output: str) -> str:
    """The most telling line of a failing check: an E-line, an error, or the last line."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("E ") and len(ln) > 4:
            return ln[1:].strip()
    for ln in lines:
        if re.search(r"(Error|error:|FAILED|Traceback)", ln):
            return ln
    return lines[-1] if lines else ""
