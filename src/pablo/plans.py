"""Plan mode: talk a plan through, then build it one verified step at a time.

In chat or voice, a request that takes several changes gets a plan instead of one run: a goal
and 3 to 8 steps, saved as `.pablo/plan.yaml` in the project. You change it by talking; the
model writes the whole plan again. Nothing runs until you say go.

Then the steps run in order, each one a normal run (Hybrid, or the model you picked, or the
one model of an unfiltered project) told what the plan is and what's done so far. Each step
waits for your approval before the next starts, because the next one builds on its code:
approve and the next step starts; reject (or a step that fails) and the plan pauses until you
say go again. `approve` stays the only way code reaches the repo.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from pablo.context import clip
from pablo.plan import PLAN_PATH, Phase, Plan, Task

if TYPE_CHECKING:
    from pablo.orchestrator import Orchestrator, Run
    from pablo.projects import Workspace

MAX_STEPS = 8


class PlanBusy(RuntimeError):
    """The plan is building; it can't be redrafted until it's paused or done."""


def title_of(task: str) -> str:
    first = task.strip().split("\n")[0]
    return clip(first, 14) if len(first) > 70 else first


def plan_text(plan: Plan) -> str:
    """The plan as a short markdown list, for the reply that drafted it."""
    lines = [f"**Plan:** {plan.goal}" if plan.goal else "**Plan:**"]
    lines += [f"{i}. {t.title}" for i, t in enumerate(Plans.steps(plan), start=1)]
    lines.append("")
    lines.append("Change anything you like, or say go and I'll build it one step at a time.")
    return "\n".join(lines)


class Plans:
    def __init__(self, ws: Workspace, *, background: bool = True) -> None:
        self.ws = ws
        self.background = background
        self._lock = threading.Lock()

    # reading ------------------------------------------------------------------------------

    def get(self, project: str = "") -> Plan | None:
        repo = self.ws.orch(project).repo
        if not (repo / PLAN_PATH).is_file():
            return None
        return Plan.load(repo)

    def _save(self, project: str, plan: Plan) -> None:
        plan.save(self.ws.orch(project).repo)

    @staticmethod
    def steps(plan: Plan) -> list[Task]:
        return [t for phase in plan.phases for t in phase.tasks]

    def summary(self, project: str = "") -> str:
        """The plan in a few lines, for the chat model's prompt ("" when there is none)."""
        plan = self.get(project)
        if plan is None or not self.steps(plan):
            return ""
        marks = {"done": "x", "active": ">", "needs_you": "?", "blocked": "!", "queued": " "}
        lines = [f"Goal: {plan.goal}  ({plan.state})"]
        for t in self.steps(plan):
            lines.append(f"[{marks.get(t.status, ' ')}] {t.id} {t.title}")
        return "\n".join(lines)

    # drafting -----------------------------------------------------------------------------

    def draft(
        self,
        project: str,
        goal: str,
        steps: list[str],
        *,
        lane: str,
        by: str,
        conversation: str,
        think: bool = False,
    ) -> Plan:
        """Replace the project's plan with a new draft. Steps already done are history: a new
        draft only replaces a plan that is a draft, paused, or done."""
        steps = [s.strip() for s in steps if s.strip()][:MAX_STEPS]
        if not steps:
            raise ValueError("a plan needs at least one step")
        name = project or self.ws.default
        with self._lock:
            old = self.get(name)
            if old is not None and old.state == "running":
                raise PlanBusy("the plan is building: pause it (reject the step) to change it")
            tasks = [
                Task(id=str(i), title=title_of(s), detail=s) for i, s in enumerate(steps, start=1)
            ]
            plan = Plan(
                project=name,
                goal=goal.strip(),
                phases=[Phase(id=1, title="Build", gate="Every step approved", tasks=tasks)],
                lane=lane,
                by=by,
                conversation=conversation,
                think=think,
            )
            self._save(name, plan)
            return plan

    # building -----------------------------------------------------------------------------

    def go(self, project: str = "", *, by: str = "") -> Plan:
        """Start (or resume) building: the next unfinished step runs."""
        name = project or self.ws.default
        with self._lock:
            plan = self.get(name)
            if plan is None or not self.steps(plan):
                raise KeyError("there's no plan to build yet")
            if plan.state == "done":
                raise ValueError("every step is done")
            waiting = next(
                (t for t in self.steps(plan) if t.status in ("active", "needs_you")), None
            )
            if waiting is not None:
                raise PlanBusy(f"step {waiting.id} is still waiting: approve or reject it first")
            for t in self.steps(plan):
                if t.status == "blocked":
                    t.status, t.run = "queued", None  # going again retries a blocked step
            plan.state = "running"
            plan.by = by or plan.by
            self._save(name, plan)
        self._next(name)
        result = self.get(name)
        assert result is not None
        return result

    def _next(self, project: str) -> None:
        with self._lock:
            plan = self.get(project)
            if plan is None or plan.state != "running":
                return
            step = next((t for t in self.steps(plan) if t.status == "queued"), None)
            if step is None:
                plan.state = "done"
                self._save(project, plan)
                return
            step.status = "active"
            self._save(project, plan)
            task = self._task_for(plan, step)
        work = threading.Thread(
            target=self._run_step, args=(project, step.id, task), name=f"plan-{project}-{step.id}",
            daemon=True,
        )  # fmt: skip
        if self.background:
            work.start()
        else:
            work.run()

    def _task_for(self, plan: Plan, step: Task) -> str:
        done = [t for t in self.steps(plan) if t.status == "done"]
        lines = [step.detail or step.title, "", f"This is step {step.id} of a plan: {plan.goal}"]
        if done:
            lines.append("Already done and in the repo: " + "; ".join(t.title for t in done))
        later = [t.title for t in self.steps(plan) if t.status == "queued" and t.id != step.id]
        if later:
            lines.append("Later steps (don't do these now): " + "; ".join(later))
        return "\n".join(lines)

    def _run_step(self, project: str, step_id: str, task: str) -> None:
        orch: Orchestrator = self.ws.orch(project)
        plan = self.get(project)
        lane = plan.lane if plan else "auto"
        unfiltered = self.ws.project(project).mode == "unfiltered"
        target, retry = (None, "now") if lane == "auto" else (lane, "off")
        if unfiltered:
            target, retry = "coder", "off"
        try:
            run = orch.start(task, lane=target, task_id=step_id, by=plan.by if plan else "",
                             retry=retry, think=bool(plan and plan.think))  # fmt: skip
        except Exception:
            self._mark(project, step_id, "blocked", None, pause=True)
            return
        if run.state in ("ready", "needs_you"):
            self._mark(project, step_id, "needs_you", run.id)
        else:  # failed: the retry (if any) is in the same group, but the plan waits for you
            self._mark(project, step_id, "blocked", run.id, pause=True)

    def _mark(
        self, project: str, step_id: str, status: str, run_id: str | None, *, pause: bool = False
    ) -> None:
        with self._lock:
            plan = self.get(project)
            if plan is None:
                return
            step = plan.task(step_id)
            step.status = status  # type: ignore[assignment]
            step.run = run_id
            if pause:
                plan.state = "paused"
            self._save(project, plan)

    def after(self, run: Run) -> None:
        """A run was approved or rejected: move its plan along. Runs outside a plan: nothing."""
        project = run.project or self.ws.default
        try:
            plan = self.get(project)
        except KeyError:
            return
        if plan is None:
            return
        orch = self.ws.orch(project)
        mine = {run.id, run.retried_from or ""}
        if run.group:  # a Hybrid retry shares its group with the run the step started
            mine |= {r.id for r in orch.recent(200) if r.group == run.group}
        step = next(
            (t for t in self.steps(plan) if t.run in mine and t.status == "needs_you"), None
        )
        if step is None:
            return
        if run.state in ("approved", "approved_partial"):
            self._mark(project, step.id, "done", run.id)
            self._next(project)
        elif run.state == "rejected":
            # The other run of a Hybrid pair is rejected when you approve one: that's not a no.
            if any(r.state.startswith("approved") for r in orch.recent(200) if r.id in mine):
                return
            self._mark(project, step.id, "blocked", run.id, pause=True)
