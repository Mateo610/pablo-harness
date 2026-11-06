"""`pablo eval`: run a lane on the fixed coder suite and score it against hidden tests.

Each task in `evals/suite/tasks.yaml` starts from a fresh copy of one seed repo. The lane works
on it like any run (context, edits, checks, judge). Then the diff is applied to another fresh
copy, the task's hidden test is written next to it, and only that test decides pass or fail.
It runs with `python -I -m pytest --noconftest`, so nothing the diff adds (a conftest.py, a
sitecustomize.py, a module that patches the code under test) can make it pass.

Runs happen in a throwaway home: their traces never reach `~/.pablo/traces`, so eval tasks can
never become training data. Results append to `evals/results.jsonl`.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import statistics
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pablo.config import Settings
from pablo.gateway import ChatBackend
from pablo.judge_replay import ReplayCase
from pablo.lessons import Lessons
from pablo.orchestrator import Orchestrator
from pablo.sandbox import Sandbox, make_sandbox
from pablo.swap import SwapView
from pablo.verify import IGNORE, apply_diff, run_check

SUITE = Path(__file__).resolve().parents[2] / "evals" / "suite"
HOLDOUT = SUITE.parent / "holdout"  # harder tasks: scored, never studied
RESULTS = SUITE.parent / "results.jsonl"
HARNESS_ROOT = SUITE.parents[1]  # this repo: git seeds are commits of it
REFS = ("file", "symbol", "none")


@dataclass(frozen=True)
class SuiteTask:
    id: str
    repo: str
    refs: str  # file | symbol | none: how the task points at the code
    kind: str
    task: str
    hidden: str
    solution: dict[str, str]

    @property
    def hidden_name(self) -> str:
        return f"test_hidden_{self.id.replace('-', '_')}.py"


@dataclass(frozen=True)
class GitSeed:
    """A seed repo taken from a pinned commit of this repo, minus `exclude` paths."""

    git: str
    exclude: tuple[str, ...] = ()


def load_suite(root: Path = SUITE) -> list[SuiteTask]:
    data = yaml.safe_load((root / "tasks.yaml").read_text("utf-8"))
    return [SuiteTask(**t) for t in data["tasks"]]


def load_seeds(root: Path = SUITE) -> dict[str, GitSeed]:
    data = yaml.safe_load((root / "tasks.yaml").read_text("utf-8"))
    return {
        name: GitSeed(str(spec["git"]), tuple(spec.get("exclude", ())))
        for name, spec in (data.get("seeds") or {}).items()
    }


def _export_commit(seed: GitSeed, dest: Path, source: Path = HARNESS_ROOT) -> None:
    """Write the files of `seed.git` into `dest` (no .git), then drop the excluded paths."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", seed.git],
        cwd=source,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        why = archive.stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"cannot read commit {seed.git[:12]} from {source}: {why} "
            "(a shallow clone? fetch full history)"
        )
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    for rel in seed.exclude:
        target = dest / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def materialize(task: SuiteTask, workdir: Path, root: Path = SUITE) -> Path:
    """A fresh git repo holding the task's seed repo, committed, ready for a run."""
    repo = workdir / task.id
    seed = load_seeds(root).get(task.repo)
    if seed is not None:
        _export_commit(seed, repo)
    else:
        shutil.copytree(root / "repos" / task.repo, repo)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=pablo", "-c", "user.email=pablo@localhost", "commit", "-qm", "seed")
    return repo


def with_python(command: list[str], python: str) -> list[str]:
    return [python, *command[1:]] if command and command[0] == "python" else list(command)


def hidden_command(task: SuiteTask, python: str = "python") -> list[str]:
    # -I: no PYTHONPATH, no cwd, no sitecustomize from the repo. --noconftest: no conftest.py.
    # -o addopts=: a pytest.ini or pyproject the diff edits can't add options. Anything that
    # stops the hidden test from being collected exits 5, which counts as a fail.
    return [python, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
            "-o", "addopts=", task.hidden_name]  # fmt: skip


def hidden_check(
    task: SuiteTask,
    repo: Path,
    diff: str,
    sandbox: Sandbox,
    *,
    files: dict[str, str] | None = None,
    python: str = "python",
    timeout_s: float = 300,
) -> tuple[bool, str]:
    """Apply `diff` (or write `files`) to a fresh copy, add the hidden test, run only that."""
    with tempfile.TemporaryDirectory(prefix="pablo-hidden-") as tmp:
        scratch = Path(tmp) / "work"
        shutil.copytree(repo, scratch, ignore=IGNORE)
        if diff:
            ok, err = apply_diff(scratch, diff)
            if not ok:
                return False, f"diff did not apply: {err}"
        for rel, text in (files or {}).items():
            (scratch / rel).parent.mkdir(parents=True, exist_ok=True)
            (scratch / rel).write_text(text, encoding="utf-8")
        (scratch / task.hidden_name).write_text(task.hidden, encoding="utf-8")
        check = run_check("hidden", hidden_command(task, python), scratch, timeout_s, sandbox)
        return check.passed, check.output_tail


@dataclass
class TaskResult:
    id: str
    refs: str
    kind: str
    state: str
    has_diff: bool
    clean: bool  # visible checks no worse than the seed
    hidden_passed: bool
    certainty: float | None
    wall_s: float
    context_files: list[str] = field(default_factory=list)
    repaired: bool = False
    fixed: bool = False  # passed or failed after a fix-on-red round
    fix_outcome: str = ""  # "" · kept · no edit blocks · didn't apply · not better
    run_id: str = ""
    hidden_tail: str = ""
    question: str = ""  # why it landed in Needs you (an edit error, a reply with no change, ...)
    retried: bool = False  # the first lane needed you, so the task was retried (cascade)
    final_lane: str = ""  # the lane whose run was scored
    explanation: str = ""  # the start of the coder's own words
    spec: str = ""  # task-derived checks: confirmed · failed · uninformative · broken · skipped

    @property
    def ready(self) -> bool:
        return self.state == "ready"


INFRA = re.compile(
    r"Server error '5\d\d|ConnectError|Connection refused|ReadTimeout|RemoteProtocolError"
    r"|Server disconnected|didn't come up"
)


class InfraError(RuntimeError):
    """The box failed, not the model: a 5xx or a dropped connection from the gateway."""


def infra_error(*runs: Any) -> str:
    """The infrastructure error behind a failed run, or "" when the model simply got it wrong."""
    for run in runs:
        q = getattr(run, "question", "") or ""
        if q.startswith("Run failed:") and INFRA.search(q):
            return q
    return ""


def preflight(backend: Any, models: list[str]) -> list[str]:
    """One tiny call to every model the suite will use, in suite order. Problems, or []."""
    problems = []
    for model in models:
        try:
            backend.complete(model, [{"role": "user", "content": "Reply with OK."}], max_tokens=8)
        except Exception as exc:
            problems.append(f"{model}: {str(exc).splitlines()[0][:200]}")
    return problems


@dataclass
class SuiteReport:
    lane: str
    model: str
    results: list[TaskResult]
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    commit: str = ""
    # Every judged diff, for the judge replay (evals/judge/replay.jsonl). Not in to_json.
    replay: list[ReplayCase] = field(default_factory=list, repr=False)

    def pass_rate(self, refs: str | None = None) -> float:
        rows = [r for r in self.results if refs is None or r.refs == refs]
        return sum(r.hidden_passed for r in rows) / len(rows) if rows else 0.0

    @property
    def false_ready(self) -> list[TaskResult]:
        """Marked ready, but the hidden test fails: the number that should stay at zero."""
        return [r for r in self.results if r.ready and not r.hidden_passed]

    @property
    def missed(self) -> list[TaskResult]:
        """The hidden test passes, but the run landed in Needs you."""
        return [r for r in self.results if r.hidden_passed and not r.ready]

    def mean_certainty(self, passed: bool) -> float | None:
        values = [
            r.certainty
            for r in self.results
            if r.certainty is not None and r.hidden_passed == passed
        ]
        return round(sum(values) / len(values), 2) if values else None

    def summary(self) -> dict[str, Any]:
        ready = [r for r in self.results if r.ready]
        return {
            "at": self.started_at,
            "commit": self.commit,
            "lane": self.lane,
            "model": self.model,
            "tasks": len(self.results),
            "pass_rate": round(self.pass_rate(), 3),
            "pass_rate_by_refs": {k: round(self.pass_rate(k), 3) for k in REFS},
            "no_diff": sum(not r.has_diff for r in self.results),
            "ready": len(ready),
            "ready_precision": round(sum(r.hidden_passed for r in ready) / len(ready), 3)
            if ready
            else None,
            "false_ready": [r.id for r in self.false_ready],
            "missed": len(self.missed),
            "retried": sum(r.retried for r in self.results),
            "retried_and_passed": sum(r.retried and r.hidden_passed for r in self.results),
            "fix_tried": sum(bool(r.fix_outcome) for r in self.results),
            "fix_outcomes": {
                o: sum(r.fix_outcome == o for r in self.results)
                for o in sorted({r.fix_outcome for r in self.results if r.fix_outcome})
            },
            "fixed": sum(r.fixed for r in self.results),
            "fixed_and_passed": sum(r.fixed and r.hidden_passed for r in self.results),
            "certainty_when_passed": self.mean_certainty(True),
            "certainty_when_failed": self.mean_certainty(False),
            "wall_s": round(sum(r.wall_s for r in self.results), 1),
            **self.spec_summary(),
        }

    def spec_summary(self) -> dict[str, Any]:
        """How task-derived checks lined up with the hidden tests (empty when they were off)."""
        ran = [r for r in self.results if r.spec]
        if not ran:
            return {}
        failed = [r for r in ran if r.spec == "failed"]
        return {
            "spec": {s: sum(r.spec == s for r in ran) for s in sorted({r.spec for r in ran})},
            # failed + hidden failed: a bug the spec caught; failed + hidden passed: a false alarm
            "spec_caught": [r.id for r in failed if not r.hidden_passed],
            "spec_false_alarm": [r.id for r in failed if r.hidden_passed],
        }

    def to_json(self) -> str:
        return json.dumps({**self.summary(), "results": [asdict(r) for r in self.results]})

    def to_markdown(self) -> str:
        s = self.summary()
        by = " · ".join(f"{k} {v:.0%}" for k, v in s["pass_rate_by_refs"].items())
        lines = [
            f"### Coder suite · {self.lane} ({self.model}) · {s['at']} · `{self.commit or '?'}`",
            "",
            f"Hidden-test pass rate {s['pass_rate']:.0%} ({by}) · no diff {s['no_diff']} · "
            f"ready {s['ready']}, of which passed {s['ready_precision'] if s['ready'] else '—'} · "
            f"false ready {len(s['false_ready'])} · missed {s['missed']} · "
            f"retried on another lane {s['retried']} ({s['retried_and_passed']} passed) · "
            f"fix rounds tried {s['fix_tried']} {s['fix_outcomes'] or ''}, "
            f"kept {s['fixed']} ({s['fixed_and_passed']} passed) · "
            f"certainty passed {s['certainty_when_passed']} "
            f"/ failed {s['certainty_when_failed']} · "
            f"wall {s['wall_s']} s"
            + (
                f" · spec {s['spec']}, caught {s['spec_caught']}, "
                f"false alarms {s['spec_false_alarm']}"
                if "spec" in s
                else ""
            ),
            "",
            "| task | refs | lane | state | diff | fixed | visible | hidden | certainty | wall s |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in self.results:
            cert = "—" if r.certainty is None else f"{r.certainty:.2f}"
            lines.append(
                f"| {r.id} | {r.refs} | {r.final_lane or self.lane} | {r.state} | "
                f"{'yes' if r.has_diff else 'no'} | "
                f"{r.fix_outcome} | "
                f"{'clean' if r.clean else 'worse'} | {'pass' if r.hidden_passed else 'FAIL'} | "
                f"{cert} | {r.wall_s:.0f} |"
            )
        return "\n".join(lines)


def current_commit(cwd: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True
        )
        return out.stdout.strip()
    except FileNotFoundError:
        return ""


def run_suite(
    settings: Settings,
    backend: ChatBackend,
    *,
    lane: str = "coder",
    only: set[str] | None = None,
    root: Path = SUITE,
    python: str = "python",
    sandbox: Sandbox | None = None,
    swap: SwapView | None = None,
    on_task: Callable[[TaskResult], None] | None = None,
    cascade: bool = False,
    spec: bool | None = None,
    learn: set[str] | None = None,
    lessons: Path | None = None,
    infra_wait_s: float = 30.0,
    pause: Callable[[float], None] = time.sleep,
) -> SuiteReport:
    """Run `lane` on every task (or `only` those ids) and score each against its hidden test.

    `cascade` turns on the lane's `retry_on` (coder → deep) right away; the task is then scored
    on the retry, which is the run you would review. Without it, retries are off, so a lane's
    score stays comparable with earlier baselines.
    """
    results: list[TaskResult] = []
    replay: list[ReplayCase] = []
    judge_model = settings.lane("judge").model
    commit = current_commit(root)
    with tempfile.TemporaryDirectory(prefix="pablo-eval-") as tmp:
        work = Path(tmp)
        # A throwaway home: eval runs and their traces disappear with this directory.
        isolated = settings.model_copy(update={"home": work / "home"}, deep=True)
        # The GPU is real: queue with every other Pablo process through the real home's table.
        isolated.gpu.shared_dir = settings.gpu.shared_dir or str(settings.home)
        for cfg in isolated.lanes.values():
            cfg.retry_when = "now" if cascade else "off"
        if spec is not None:
            isolated.spec.enabled = spec
        # Learning, measured: turn on `learn` pieces and start from a lessons folder made by
        # practice on the seed repos (never from real use, never from suite tasks).
        for piece in learn or set():
            if piece not in ("examples", "failures", "routing"):
                raise ValueError(f"unknown learning piece {piece!r}: examples, failures, routing")
            setattr(isolated.learn, piece, True)
        if lessons is not None:
            shutil.copytree(lessons, isolated.home / "lessons", dirs_exist_ok=True)
        # Evals are the only user: no waiting for quiet, and the owner's mode doesn't apply.
        isolated.gpu.idle_s = 0
        isolated.gpu.mode = "balanced"
        cfg = isolated.lane(lane)
        if cfg.kind == "remote" or cfg.requires_approval:
            # Scoring Claude by hand (`--allow-remote`): each task goes out as an escalation, the
            # one way the harness ever calls it. Traces stay in this throwaway home, untrainable.
            def start(o: Orchestrator, text: str) -> Any:
                return o.start(text, escalate=True)
        else:

            def start(o: Orchestrator, text: str) -> Any:
                return o.start(text, lane=lane)

        for task in load_suite(root):
            if only and task.id not in only:
                continue
            repo = materialize(task, work, root)
            box = sandbox or make_sandbox(settings.sandbox, repo)
            checks = {name: with_python(cmd, python) for name, cmd in _repo_checks(repo).items()}
            orch = Orchestrator(isolated, backend, repo=repo, checks=checks, sandbox=box, swap=swap)
            orch.retry_inline = True
            orch.repo_key = task.repo  # lessons belong to the seed repo, not the task's dir
            orch.lessons = Lessons(isolated.home, frozen=True)
            started = time.perf_counter()
            first = start(orch, task.task)
            run = orch.get(first.retry_run) if first.retry_run else first
            if infra_error(first, run):
                # A 500 or a dropped connection says nothing about the model. Give the box a
                # moment and try the task once more (runs never touch the repo, so it's the same
                # task); if it fails the same way, stop: the rest of the suite would be noise.
                pause(infra_wait_s)
                started = time.perf_counter()
                first = start(orch, task.task)
                run = orch.get(first.retry_run) if first.retry_run else first
                if why := infra_error(first, run):
                    raise InfraError(f"{task.id}: {why[:300]}")
            wall = time.perf_counter() - started
            passed, tail = (
                hidden_check(task, repo, run.diff, box, python=python)
                if run.diff
                else (False, "no diff")
            )
            context: list[str] = next(
                (e["data"].get("files", []) for e in orch.events(run.id) if e["kind"] == "context"),
                [],
            )
            result = TaskResult(
                id=task.id,
                refs=task.refs,
                kind=task.kind,
                state=run.state,
                has_diff=bool(run.diff),
                clean=bool(run.verdict and run.verdict.get("clean")),
                hidden_passed=passed,
                certainty=run.certainty,
                wall_s=round(wall, 1),
                context_files=list(context),
                repaired=run.repaired,
                fixed=run.fixed,
                fix_outcome=run.fix_outcome,
                run_id=run.id,
                hidden_tail="" if passed else tail[-600:],
                question=run.question[:400],
                explanation=run.explanation[:300],
                retried=bool(first.retry_run),
                final_lane=run.lane,
                spec=(run.spec or {}).get("status", ""),
            )
            results.append(result)
            if run.diff and run.verdict and run.certainty is not None:
                replay.append(
                    ReplayCase(
                        task_id=task.id,
                        task=task.task,
                        diff=run.diff,
                        verdict=run.verdict,
                        passed=passed,
                        lane=run.lane,
                        commit=commit,
                        judge_model=judge_model,
                        certainty=run.certainty,
                    )
                )
            if on_task:
                on_task(result)
    return SuiteReport(
        lane=f"{lane}→{settings.lane(lane).retry_on}"
        if cascade and settings.lane(lane).retry_on
        else lane,
        model=settings.lane(lane).model,
        results=results,
        commit=commit,
        replay=replay,
    )


def _repo_checks(repo: Path) -> dict[str, list[str]]:
    from pablo.demo import load_checks

    return load_checks(repo)


def aggregate(reports: list[SuiteReport]) -> dict[str, Any]:
    """Several runs of the same suite: mean, spread, and which tasks flip between runs."""
    rates = [r.pass_rate() for r in reports]
    passes: dict[str, int] = {}
    for report in reports:
        for row in report.results:
            passes[row.id] = passes.get(row.id, 0) + int(row.hidden_passed)
    n = len(reports)
    return {
        "runs": n,
        "pass_counts": [sum(r.hidden_passed for r in rep.results) for rep in reports],
        "mean_pass_rate": round(statistics.mean(rates), 3) if rates else 0.0,
        "stdev_pass_rate": round(statistics.stdev(rates), 3) if n > 1 else 0.0,
        "always": sorted(k for k, v in passes.items() if v == n),
        "never": sorted(k for k, v in passes.items() if v == 0),
        "flaky": sorted(k for k, v in passes.items() if 0 < v < n),
        "false_ready_total": sum(len(r.false_ready) for r in reports),
    }


def aggregate_markdown(reports: list[SuiteReport]) -> str:
    a = aggregate(reports)
    head = reports[0] if reports else None
    title = (
        f"### Coder suite × {a['runs']} · {head.lane} ({head.model}) · `{head.commit}`"
        if head
        else ""
    )
    return "\n".join(
        [
            title,
            "",
            f"Passed per run {a['pass_counts']} · mean {a['mean_pass_rate']:.0%} "
            f"± {a['stdev_pass_rate']:.0%} · false ready over all runs {a['false_ready_total']}",
            "",
            f"- Flaky ({len(a['flaky'])}): {', '.join(a['flaky']) or 'none'}",
            f"- Never passed ({len(a['never'])}): {', '.join(a['never']) or 'none'}",
        ]
    )


HOLDOUT_RESULTS = SUITE.parent / "holdout-results.jsonl"


def sealed_summary(report: SuiteReport) -> dict[str, Any]:
    """A holdout run as totals only: no task ids, no outputs, nothing to study."""
    s = report.summary()
    keep = (
        "at", "commit", "lane", "model", "tasks", "pass_rate", "pass_rate_by_refs", "no_diff",
        "ready", "ready_precision", "missed", "retried", "retried_and_passed", "wall_s",
    )  # fmt: skip
    return {"set": "holdout", **{k: s[k] for k in keep}, "false_ready": len(s["false_ready"])}


def append_sealed(report: SuiteReport, path: Path = HOLDOUT_RESULTS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(sealed_summary(report)) + "\n")


def sealed_markdown(reports: list[SuiteReport]) -> str:
    rows = [sealed_summary(r) for r in reports]
    rates = [r["pass_rate"] for r in rows]
    spread = f" ± {statistics.stdev(rates):.0%}" if len(rates) > 1 else ""
    first = rows[0]
    return (
        f"### Holdout · {first['lane']} ({first['model']}) · `{first['commit']}`\n\n"
        f"Passed per run {[round(r * first['tasks']) for r in rates]} of {first['tasks']} · mean "
        f"{statistics.mean(rates):.0%}{spread} · false ready {[r['false_ready'] for r in rows]} · "
        f"wall {[r['wall_s'] for r in rows]} s"
    )


def append_result(report: SuiteReport, path: Path = RESULTS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(report.to_json() + "\n")
