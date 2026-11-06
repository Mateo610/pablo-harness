"""Judge replay: score a judge on real suite diffs, labeled by the hidden tests.

`pablo eval --write` keeps every judged diff from the suite (task, diff, the checks the judge
saw, and whether the hidden test passed) in evals/judge/replay.jsonl. `pablo eval-judge --set
replay --model X` sends those same diffs and checks to model X and counts how often it calls a
broken diff ready. That is the number the judge bake-off picks on: the hand-written cases in
evals/judge were tuned on, these were not.

The hidden test's output is never stored or shown to a judge: only pass or fail, as the label.
Nothing here is training data; `prepare_training` reads traces, never this file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from pablo.gateway import ChatBackend
from pablo.judge import judge
from pablo.verify import Check, Verdict

REPLAY = Path(__file__).resolve().parents[2] / "evals" / "judge" / "replay.jsonl"


@dataclass
class ReplayCase:
    task_id: str
    task: str
    diff: str
    verdict: dict[str, Any]
    passed: bool  # the hidden test: the label, never shown to the judge
    lane: str = ""
    commit: str = ""
    judge_model: str = ""
    certainty: float | None = None  # what the judge said live
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def key(self) -> str:
        """Same task, same diff: one case, however many runs produced it."""
        digest = hashlib.sha256(self.diff.encode("utf-8")).hexdigest()[:16]
        return f"{self.task_id}:{digest}"


def verdict_from_dict(data: dict[str, Any]) -> Verdict:
    check_fields = {f.name for f in fields(Check)}
    checks = [Check(**{k: v for k, v in c.items() if k in check_fields}) for c in data["checks"]]
    keep = {f.name for f in fields(Verdict)} - {"checks"}
    return Verdict(checks=checks, **{k: v for k, v in data.items() if k in keep})


def append_cases(cases: list[ReplayCase], path: Path = REPLAY) -> int:
    """Append cases not already in the file. Returns how many were new."""
    seen = {c.key for c in load_cases(path)}
    new = [c for c in cases if c.key not in seen]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for case in new:
                fh.write(json.dumps(asdict(case)) + "\n")
    return len(new)


def load_cases(path: Path = REPLAY) -> list[ReplayCase]:
    if not path.is_file():
        return []
    out: dict[str, ReplayCase] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = ReplayCase(**json.loads(line))
            out.setdefault(case.key, case)
    return list(out.values())


@dataclass
class ReplayRow:
    key: str
    task_id: str
    passed: bool
    clean: bool
    certainty: float
    ready: bool


@dataclass
class ReplayReport:
    model: str
    threshold: float
    rows: list[ReplayRow]

    @property
    def false_ready(self) -> list[ReplayRow]:
        """Called ready, hidden test failed: the mistake that costs you."""
        return [r for r in self.rows if r.ready and not r.passed]

    @property
    def missed(self) -> list[ReplayRow]:
        """Sent to you, hidden test passed: costs a look, not a bug."""
        return [r for r in self.rows if not r.ready and r.passed]

    def summary(self) -> dict[str, Any]:
        bad = [r for r in self.rows if not r.passed]
        good = [r for r in self.rows if r.passed]
        ready = [r for r in self.rows if r.ready]
        # Broken diffs whose checks were clean: only the judge can catch these.
        sneaky = [r for r in bad if r.clean]
        return {
            "cases": len(self.rows),
            "broken": len(bad),
            "false_ready": len(self.false_ready),
            "caught": round(1 - len(self.false_ready) / len(bad), 3) if bad else 1.0,
            "sneaky": len(sneaky),
            "sneaky_caught": sum(not r.ready for r in sneaky),
            "missed": len(self.missed),
            "ready_precision": round(sum(r.passed for r in ready) / len(ready), 3)
            if ready
            else 0.0,
            "certainty_when_passed": round(mean(r.certainty for r in good), 2) if good else 0.0,
            "certainty_when_failed": round(mean(r.certainty for r in bad), 2) if bad else 0.0,
        }

    def sweep(
        self, cutoffs: tuple[float, ...] = (0.5, 0.6, 0.7, 0.75, 0.8, 0.9)
    ) -> list[dict[str, float]]:
        """False ready and missed at other thresholds, from the same certainties (no new calls).

        Models calibrate differently; compare judges at their best cutoff, then set
        thresholds.judge_needs_you to the winner's.
        """
        out = []
        for t in cutoffs:
            ready = [r.certainty >= t for r in self.rows]
            out.append(
                {
                    "threshold": t,
                    "false_ready": sum(
                        k and not r.passed for k, r in zip(ready, self.rows, strict=True)
                    ),
                    "missed": sum(
                        not k and r.passed for k, r in zip(ready, self.rows, strict=True)
                    ),
                }
            )
        return out

    def to_markdown(self) -> str:
        s = self.summary()
        lines = [
            f"### Judge replay · {self.model} · {s['cases']} suite diffs ({s['broken']} broken)",
            "",
            f"False ready **{s['false_ready']}** (caught {s['caught']:.0%} of broken) · "
            f"clean-but-broken caught {s['sneaky_caught']}/{s['sneaky']} · "
            f"missed {s['missed']} · ready precision {s['ready_precision']} · "
            f"certainty passed {s['certainty_when_passed']} / failed "
            f"{s['certainty_when_failed']} · threshold {self.threshold}",
        ]
        if self.false_ready:
            lines += ["", "False ready: " + ", ".join(r.task_id for r in self.false_ready)]
        sweep = self.sweep()
        lines += [
            "",
            "| threshold | " + " | ".join(str(x["threshold"]) for x in sweep) + " |",
            "|---|" + "---|" * len(sweep),
            "| false ready | " + " | ".join(str(x["false_ready"]) for x in sweep) + " |",
            "| missed | " + " | ".join(str(x["missed"]) for x in sweep) + " |",
        ]
        return "\n".join(lines)


def run_replay(
    backend: ChatBackend,
    model: str,
    *,
    threshold: float = 0.75,
    path: Path = REPLAY,
    only: set[str] | None = None,
    max_tokens: int = 600,
) -> ReplayReport:
    rows = []
    for case in load_cases(path):
        if only and case.task_id not in only:
            continue
        verdict = verdict_from_dict(case.verdict)
        judgement = judge(backend, model, case.task, case.diff, verdict, max_tokens=max_tokens)
        rows.append(
            ReplayRow(
                key=case.key,
                task_id=case.task_id,
                passed=case.passed,
                clean=verdict.clean,
                certainty=judgement.certainty,
                ready=judgement.certainty >= threshold,
            )
        )
    return ReplayReport(model, threshold, rows)
