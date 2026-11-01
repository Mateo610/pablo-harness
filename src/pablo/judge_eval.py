"""Measure the judge on fixed fixtures: does certainty separate good diffs from bad ones?

Fixtures live in evals/judge (a tiny project plus cases that rewrite its files).
Each case becomes a real git diff, runs the real checks, then goes to the judge.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

from pablo.gateway import ChatBackend
from pablo.judge import judge
from pablo.verify import verify_diff

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "judge"
CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}


@dataclass
class CaseResult:
    id: str
    expect: str
    checks_passed: bool
    certainty: float
    correct: bool
    summary: str


@dataclass
class JudgeReport:
    results: list[CaseResult]
    threshold: float

    @property
    def accuracy(self) -> float:
        return sum(r.correct for r in self.results) / len(self.results) if self.results else 0.0

    def mean_certainty(self, expect: str) -> float:
        values = [r.certainty for r in self.results if r.expect == expect]
        return mean(values) if values else 0.0

    @property
    def tests_passed_but_bad(self) -> list[CaseResult]:
        return [r for r in self.results if r.expect == "bad" and r.checks_passed]

    def to_markdown(self, model: str) -> str:
        lines = [
            f"### Judge eval · {model}",
            "",
            f"Accuracy {self.accuracy:.0%} at threshold {self.threshold} · mean certainty "
            f"good {self.mean_certainty('good'):.2f}, bad {self.mean_certainty('bad'):.2f}",
            "",
            "| case | expect | checks | certainty | right? |",
            "|---|---|---|---|---|",
        ]
        for r in self.results:
            lines.append(
                f"| {r.id} | {r.expect} | {'pass' if r.checks_passed else 'fail'} | "
                f"{r.certainty:.2f} | {'yes' if r.correct else 'NO'} |"
            )
        return "\n".join(lines) + "\n"


def load_cases(root: Path = FIXTURES) -> list[dict[str, Any]]:
    data = yaml.safe_load((root / "cases.yaml").read_text("utf-8"))
    return list(data["cases"])


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout


def build_case(case: dict[str, Any], workdir: Path, root: Path = FIXTURES) -> tuple[Path, str]:
    """Copy the base project into a fresh git repo and return (repo, diff for this case)."""
    repo = workdir / case["id"]
    shutil.copytree(root / "repo", repo)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=pablo", "-c", "user.email=pablo@localhost", "commit", "-qm", "base")
    for rel, text in case["files"].items():
        (repo / rel).write_text(text, encoding="utf-8")
    diff = _git(repo, "diff")
    _git(repo, "checkout", "--", ".")
    return repo, diff


def run_judge_eval(
    backend: ChatBackend,
    model: str,
    *,
    threshold: float = 0.75,
    root: Path = FIXTURES,
    only: set[str] | None = None,
) -> JudgeReport:
    results = []
    with tempfile.TemporaryDirectory(prefix="pablo-judge-eval-") as tmp:
        for case in load_cases(root):
            if only and case["id"] not in only:
                continue
            repo, diff = build_case(case, Path(tmp), root)
            verdict = verify_diff(repo, diff, CHECKS)
            judgement = judge(backend, model, case["task"], diff, verdict)
            good = judgement.certainty >= threshold
            results.append(
                CaseResult(
                    id=case["id"],
                    expect=case["expect"],
                    checks_passed=verdict.passed,
                    certainty=judgement.certainty,
                    correct=good == (case["expect"] == "good"),
                    summary=judgement.summary,
                )
            )
    return JudgeReport(results, threshold)
