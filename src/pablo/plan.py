"""GSD-style project state: phases, tasks, and gates.

A plan lives in the repo it describes, so it travels with the code, diffs in git, and any
model (or you) can read it. A phase closes only when its gate passes.

- `.pablo/plan.yaml` is the project's plan: the mission board, `pablo plan`, the overnight
  queue, and anything that doesn't say which conversation it's for.
- `.pablo/plans/<conversation>.yaml` is a plan made in a chat (C14): one per conversation, so
  two chats can each plan and build in the same project. A Major plan also keeps its spec,
  roadmap and state as markdown in `.pablo/plans/<conversation>/`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Status = Literal["queued", "active", "needs_you", "done", "blocked", "skipped"]
FINISHED = ("done", "skipped")  # a skipped step is over: you chose to move on without it
PlanState = Literal["draft", "running", "paused", "done"]
PlanSize = Literal["", "minor", "major"]
PLAN_PATH = Path(".pablo") / "plan.yaml"
PLANS_DIR = Path(".pablo") / "plans"
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def plan_file(root: Path, key: str = "") -> Path:
    """Where a plan lives: the project's plan, or the plan of conversation `key`."""
    if not key:
        return root / PLAN_PATH
    if not _KEY.match(key):
        raise ValueError(f"bad plan key: {key!r}")
    return root / PLANS_DIR / f"{key}.yaml"


class Task(BaseModel):
    id: str
    title: str
    status: Status = "queued"
    lane: str | None = None
    notes: str = ""
    detail: str = ""  # the full task the coder gets (plan mode); the title is its first words
    run: str | None = None  # the run doing this step, once it has started


class Phase(BaseModel):
    id: int
    title: str
    gate: str
    tasks: list[Task] = Field(default_factory=list)

    @property
    def done(self) -> bool:
        return bool(self.tasks) and all(t.status in FINISHED for t in self.tasks)

    def progress(self) -> tuple[int, int]:
        return sum(t.status in FINISHED for t in self.tasks), len(self.tasks)


class Plan(BaseModel):
    project: str
    goal: str = ""
    phases: list[Phase] = Field(default_factory=list)
    # Plan mode: a draft you talk through, then running one step at a time, each waiting for
    # your approval; paused when a step is rejected or fails; done when every step is.
    state: PlanState = "draft"
    lane: str = "auto"  # which model builds it: "auto" is Hybrid
    by: str = ""  # who started it
    conversation: str = ""  # where it was planned, so progress can be reported there
    think: bool = False  # extended thinking: each step's coder thinks first
    request: str = ""  # the user's words the plan came from: every step sees them
    size: PlanSize = ""  # Plan mode's size when it was drafted there ("" from Auto)
    spec: str = ""  # a Major plan's spec, as the user approved it (markdown)
    updated: str = ""  # when it was last saved (ISO, UTC)

    @classmethod
    def load(cls, root: Path, key: str = "") -> Plan:
        text = plan_file(root, key).read_text(encoding="utf-8")
        return cls.model_validate(yaml.safe_load(text))

    def save(self, root: Path, key: str = "") -> Path:
        path = plan_file(root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated = datetime.now(UTC).isoformat(timespec="seconds")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        tmp.replace(path)  # a reader polling the plan never sees half a file
        return path

    def current_phase(self) -> Phase | None:
        return next((p for p in self.phases if not p.done), None)

    def next_task(self) -> Task | None:
        phase = self.current_phase()
        if phase is None:
            return None
        for wanted in ("needs_you", "active", "queued"):
            for task in phase.tasks:
                if task.status == wanted:
                    return task
        return None

    def task(self, task_id: str) -> Task:
        for phase in self.phases:
            for task in phase.tasks:
                if task.id == task_id:
                    return task
        raise KeyError(f"no task '{task_id}' in plan")

    def set_status(self, task_id: str, status: Status) -> Task:
        task = self.task(task_id)
        task.status = status
        return task


def starter_plan(project: str) -> Plan:
    return Plan(
        project=project,
        goal="Describe what done looks like in one sentence.",
        phases=[
            Phase(
                id=1,
                title="First slice",
                gate="One real task runs end to end and passes its checks",
                tasks=[
                    Task(id="1.1", title="Write the first failing test"),
                    Task(id="1.2", title="Make it pass"),
                ],
            )
        ],
    )
