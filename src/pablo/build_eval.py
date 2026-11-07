"""`pablo eval-build`: does plan mode build a small tool that works?

For each project in `evals/build/tasks.yaml`, in a throwaway home:

1. A fresh, empty project is made, and the request is sent in Chat (Hybrid by default) with
   "plan it first". If no plan comes back, Pablo is asked once more to write it as a plan.
2. "Looks good, go." builds it. Each step that waits is decided the way a person trusting the
   checks would: approve a ready change, or one with clean checks and a diff (counted
   separately, since the judge wasn't sure); otherwise reject it, which pauses the plan, and go
   once more. A second stop ends the project.
3. The acceptance test runs in the final repo (`python -I -m pytest --noconftest`).

It measures what the suite can't: whether the model plans when asked, how many steps a small
tool takes, how often a step needs you, and whether the end result works.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pablo.config import Settings
from pablo.gateway import ChatBackend
from pablo.sandbox import Sandbox

BUILD = Path(__file__).resolve().parents[2] / "evals" / "build"
BUILD_RESULTS = BUILD.parent / "build-results.jsonl"
ACCEPTANCE = "test_acceptance.py"
MAX_DECISIONS = 12


@dataclass(frozen=True)
class BuildTask:
    id: str
    title: str
    prompt: str
    acceptance: str
    solution: dict[str, str] = field(default_factory=dict)


@dataclass
class BuildResult:
    id: str
    planned: bool = False  # a plan came back (first ask or second)
    asked_twice: bool = False  # the first reply wasn't a plan
    steps: int = 0
    approved: int = 0
    approved_unsure: int = 0  # clean checks, but the judge wanted you
    rejected: int = 0
    pressed_build: bool = False  # "go" didn't start it; the Build button did
    finished: bool = False  # every step approved
    accepted: bool = False  # the acceptance test passed
    acceptance_tail: str = ""
    wall_s: float = 0.0
    error: str = ""


def load_build(root: Path = BUILD) -> list[BuildTask]:
    data = yaml.safe_load((root / "tasks.yaml").read_text("utf-8"))
    return [BuildTask(**p) for p in data["projects"]]


def acceptance_check(
    task: BuildTask, repo: Path, sandbox: Sandbox, *, files: dict[str, str] | None = None,
    python: str = "python",
) -> tuple[bool, str]:  # fmt: skip
    from pablo.verify import run_check

    with tempfile.TemporaryDirectory(prefix="pablo-accept-") as tmp:
        scratch = Path(tmp) / "work"
        shutil.copytree(repo, scratch, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for rel, text in (files or {}).items():
            (scratch / rel).parent.mkdir(parents=True, exist_ok=True)
            (scratch / rel).write_text(text, encoding="utf-8")
        (scratch / ACCEPTANCE).write_text(task.acceptance, encoding="utf-8")
        cmd = [python, "-I", "-m", "pytest", "-q", "--noconftest", "-p", "no:cacheprovider",
               ACCEPTANCE]  # fmt: skip
        check = run_check("acceptance", cmd, scratch, 300, sandbox)
        return check.passed, check.output_tail


def _decide(ws: Any, plans: Any, project: str, result: BuildResult, conversation: str = "") -> bool:
    """Decide the step that waits. True if it was approved."""
    from pablo.plans import Plans

    plan = plans.get(project, conversation)
    step = next((t for t in Plans.steps(plan) if t.status == "needs_you" and t.run), None)
    if step is None:
        return False
    orch = ws.orch(project)
    first = orch.get(step.run)
    family = [first] + [r for r in orch.recent(200) if first.group and r.group == first.group
                        and r.id != first.id]  # fmt: skip
    ready = next((r for r in family if r.state == "ready" and r.diff), None)
    clean = next(
        (r for r in family if r.state == "needs_you" and r.diff and r.verdict
         and r.verdict.get("clean")), None,
    )  # fmt: skip
    pick = ready or clean
    if pick is not None:
        done = orch.approve(pick.id)
        result.approved += 1
        result.approved_unsure += pick is clean
        plans.after(done)
        return True
    done = orch.reject(first.id)
    result.rejected += 1
    plans.after(done)
    return False


def run_build(
    settings: Settings,
    backend: ChatBackend,
    *,
    lane: str = "auto",
    only: set[str] | None = None,
    root: Path = BUILD,
    sandbox: Sandbox | None = None,
    python: str = "python",
    on_result: Any = None,
) -> list[BuildResult]:
    from pablo.convos import Chat
    from pablo.orchestrator import Orchestrator
    from pablo.plans import Plans
    from pablo.projects import Workspace

    results: list[BuildResult] = []
    with tempfile.TemporaryDirectory(prefix="pablo-build-") as tmp:
        work = Path(tmp)
        isolated = settings.model_copy(update={"home": work / "home"}, deep=True)
        isolated.gpu.shared_dir = settings.gpu.shared_dir or str(settings.home)
        isolated.gpu.idle_s = 0
        isolated.gpu.mode = "balanced"
        isolated.projects.root = str(work / "projects")
        for cfg in isolated.lanes.values():
            cfg.retry_when = "off"  # Hybrid asks for its own retry; nothing else retries
        anchor = work / "anchor"  # the workspace's default project: never used for work
        anchor.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=anchor, check=True)
        for task in load_build(root):
            if only and task.id not in only:
                continue
            result = BuildResult(task.id)
            started = time.perf_counter()
            try:
                base = Orchestrator(isolated, backend, repo=anchor, checks={}, sandbox=sandbox)
                base.retry_inline = True
                ws = Workspace(base)
                project = ws.create(task.title, task.prompt)
                plans = Plans(ws, background=False)
                chat = Chat(base, workspace=ws, plans=plans)
                conv = chat.start(project=project.name, lane=lane)
                turn = chat.say(conv.id, f"{task.prompt}\n\nPlan it first; don't build yet.")
                if turn.plan is None:
                    result.asked_twice = True
                    turn = chat.say(conv.id, "Please write that as a plan, with PLAN and STEP "
                                             "lines, and don't build yet.")  # fmt: skip
                plan = plans.get(project.name, conv.id)
                result.planned = plan is not None
                if plan is not None:
                    result.steps = len(Plans.steps(plan))
                    chat.say(conv.id, "Looks good, go.")
                    if (plans.get(project.name, conv.id) or plan).state == "draft":
                        result.pressed_build = True  # the model didn't write BUILD: go
                        plans.go(project.name, by="owner", conversation=conv.id)
                    stops = 0
                    for _ in range(MAX_DECISIONS):
                        plan = plans.get(project.name, conv.id)
                        if plan is None or plan.state == "done":
                            break
                        if plan.state == "paused":
                            stops += 1
                            if stops > 1:
                                break
                            plans.go(project.name, by="owner", conversation=conv.id)
                            continue
                        if not _decide(ws, plans, project.name, result, conv.id):
                            plan = plans.get(project.name, conv.id)
                            if plan is not None and plan.state == "running":
                                break  # nothing waiting and not paused: stuck
                    plan = plans.get(project.name, conv.id)
                    result.finished = plan is not None and plan.state == "done"
                    if plan is not None:
                        result.steps = len(Plans.steps(plan))  # as built, if "go" changed it
                box = sandbox or ws.orch(project.name).sandbox
                result.accepted, tail = acceptance_check(
                    task, Path(project.path), box, python=python
                )
                result.acceptance_tail = "" if result.accepted else tail[-600:]
            except Exception as exc:  # one broken project mustn't stop the set
                result.error = str(exc)[:300]
            result.wall_s = round(time.perf_counter() - started, 1)
            results.append(result)
            if on_result:
                on_result(result)
    return results


def build_summary(results: list[BuildResult], *, lane: str, commit: str) -> dict[str, Any]:
    n = len(results) or 1
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": commit,
        "lane": lane,
        "projects": len(results),
        "accepted": sum(r.accepted for r in results),
        "planned": sum(r.planned for r in results),
        "asked_twice": sum(r.asked_twice for r in results),
        "pressed_build": sum(r.pressed_build for r in results),
        "finished": sum(r.finished for r in results),
        "mean_steps": round(statistics.mean([r.steps for r in results]), 1) if results else 0,
        "approved_unsure": sum(r.approved_unsure for r in results),
        "rejected": sum(r.rejected for r in results),
        "accept_rate": round(sum(r.accepted for r in results) / n, 3),
        "wall_s": round(sum(r.wall_s for r in results), 1),
        "results": [asdict(r) for r in results],
    }


def append_build(summary: dict[str, Any], path: Path = BUILD_RESULTS) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")


def build_markdown(summary: dict[str, Any]) -> str:
    rows = "\n".join(
        f"| {r['id']} | {'yes' if r['planned'] else 'no'}{' (2nd ask)' if r['asked_twice'] else ''}"
        f" | {r['steps']} | {r['approved']} ({r['approved_unsure']} unsure) | {r['rejected']} | "
        f"{'yes' if r['finished'] else 'no'} | {'**pass**' if r['accepted'] else 'fail'} | "
        f"{r['wall_s']:.0f} s |"
        for r in summary["results"]
    )
    return (
        f"### Build set · {summary['lane']} · `{summary['commit']}`\n\n"
        f"Accepted {summary['accepted']}/{summary['projects']} · planned {summary['planned']} "
        f"(asked twice {summary['asked_twice']}) · finished {summary['finished']} · mean steps "
        f"{summary['mean_steps']} · wall {summary['wall_s']} s\n\n"
        "| Project | Planned | Steps | Approved | Rejected | Finished | Acceptance | Wall |\n"
        "|---|---|---|---|---|---|---|---|\n" + rows
    )
