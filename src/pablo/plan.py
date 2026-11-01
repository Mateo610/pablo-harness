"""GSD-style project state: phases, tasks, and gates in .pablo/plan.yaml.

The plan lives in the repo it describes, so it travels with the code, diffs in
git, and any model (or you) can read it. A phase closes only when its gate
passes; the cockpit's mission board renders this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Status = Literal["queued", "active", "needs_you", "done", "blocked"]
PLAN_PATH = Path(".pablo") / "plan.yaml"


class Task(BaseModel):
    id: str
    title: str
    status: Status = "queued"
    lane: str | None = None
    notes: str = ""


class Phase(BaseModel):
    id: int
    title: str
    gate: str
    tasks: list[Task] = Field(default_factory=list)

    @property
    def done(self) -> bool:
        return bool(self.tasks) and all(t.status == "done" for t in self.tasks)

    def progress(self) -> tuple[int, int]:
        return sum(t.status == "done" for t in self.tasks), len(self.tasks)


class Plan(BaseModel):
    project: str
    goal: str = ""
    phases: list[Phase] = Field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> Plan:
        return cls.model_validate(yaml.safe_load((root / PLAN_PATH).read_text(encoding="utf-8")))

    def save(self, root: Path) -> Path:
        path = root / PLAN_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
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
