"""Plan mode: talk a plan through, then build it one verified step at a time.

In chat or voice, a request that takes several changes gets a plan instead of one run: a goal
and 3 to 8 steps (a Major plan: phases, up to 24 steps). A plan made in a conversation is saved
as `.pablo/plans/<conversation>.yaml`, so two chats can each have one in the same project; a
plan made without one is the project's `.pablo/plan.yaml`. You change it by talking; the model
writes the whole plan again. Nothing runs until you say go.

Then the steps run in order, each one a normal run (Hybrid, or the model you picked, or the
one model of an unfiltered project) told what the plan is and what's done so far. Each step
waits for your approval before the next starts, because the next one builds on its code:
approve and the next step starts; reject (or a step that fails) and the plan pauses until you
say go again. `approve` stays the only way code reaches the repo.
"""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from pablo.plan import FINISHED, PLAN_PATH, PLANS_DIR, Phase, Plan, PlanSize, Task, plan_file

if TYPE_CHECKING:
    from pablo.orchestrator import Orchestrator, Run
    from pablo.projects import Workspace

MAX_STEPS = 8
ADVISOR = "advisor"  # a plan drafted in an Advisor conversation runs on the Advisor alone
MAX_MAJOR_STEPS = 24
MAX_PHASES = 6


_PHASE = re.compile(r"^\s*PHASE:\s*(.+?)\s*$")
_STEP_LINE = re.compile(r"^\s*STEP:\s*(.+?)\s*$")


def parse_phases(answer: str) -> list[tuple[str, str, list[str]]] | None:
    """`PHASE: <title> | <done when>` lines, each followed by its STEP lines (a Major plan).
    None when the answer has no PHASE line: its steps are one phase."""
    phases: list[tuple[str, str, list[str]]] = []
    for line in answer.splitlines():
        phase = _PHASE.match(line)
        if phase:
            title, _, gate = phase.group(1).partition("|")
            phases.append((title.strip(), gate.strip(), []))
            continue
        step = _STEP_LINE.match(line)
        if step:
            if not phases:
                phases.append(("", "", []))
            phases[-1][2].append(step.group(1))
    return phases if any(t or g for t, g, _ in phases) else None


class PlanBusy(RuntimeError):
    """The plan is building; it can't be redrafted until it's paused or done."""


TITLE_CHARS = 70


def title_of(task: str) -> str:
    """A step's first words: its first line, cut at a word near 70 characters. (clip() keeps
    whole lines, so on one long line it kept nothing.)"""
    first = task.strip().split("\n")[0].strip()
    if len(first) <= TITLE_CHARS:
        return first
    cut = first[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:(")
    return (cut or first[:TITLE_CHARS]) + "…"


def plan_text(plan: Plan) -> str:
    """The plan as a short markdown list, for the reply that drafted it."""
    lines = [f"**Plan:** {plan.goal}" if plan.goal else "**Plan:**"]
    if len(plan.phases) > 1:
        for phase in plan.phases:
            lines.append(f"\n**Phase {phase.id}: {phase.title}** (done when: {phase.gate})")
            lines += [f"{t.id}. {t.title}" for t in phase.tasks]
    else:
        lines += [f"{i}. {t.title}" for i, t in enumerate(Plans.steps(plan), start=1)]
    lines.append("")
    if plan.size:
        lines.append("Change anything you like. When it's right, approve it and Auto builds it.")
    else:
        lines.append("Change anything you like, or say go and I'll build it one step at a time.")
    return "\n".join(lines)


def roadmap_md(plan: Plan) -> str:
    lines = [f"# Roadmap: {plan.goal or 'untitled'}", ""]
    for phase in plan.phases:
        done, total = phase.progress()
        lines += [f"## Phase {phase.id}: {phase.title}", f"Done when: {phase.gate}",
                  f"Progress: {done}/{total}", ""]  # fmt: skip
        lines += [f"- [{'x' if t.status in FINISHED else ' '}] {t.id} {t.detail or t.title}"
                  + (" (skipped)" if t.status == "skipped" else "")
                  for t in phase.tasks]  # fmt: skip
        lines.append("")
    return "\n".join(lines)


def state_md(plan: Plan) -> str:
    steps = Plans.steps(plan)
    done = sum(t.status in FINISHED for t in steps)
    nxt = next((t for t in steps if t.status not in FINISHED), None)
    lines = [f"# State: {plan.goal or 'untitled'}", "", f"- State: {plan.state}",
             f"- Progress: {done}/{len(steps)} steps",
             f"- Next: {f'{nxt.id} {nxt.title} ({nxt.status})' if nxt else 'nothing, all done'}",
             f"- Updated: {plan.updated}"]  # fmt: skip
    return "\n".join(lines) + "\n"


def write_docs(root: Path, key: str, plan: Plan) -> None:
    """A Major plan keeps GSD-style markdown next to its yaml: SPEC, ROADMAP and STATE."""
    folder = root / PLANS_DIR / key
    folder.mkdir(parents=True, exist_ok=True)
    if plan.spec:
        (folder / "SPEC.md").write_text(plan.spec.strip() + "\n", encoding="utf-8")
    (folder / "ROADMAP.md").write_text(roadmap_md(plan), encoding="utf-8")
    (folder / "STATE.md").write_text(state_md(plan), encoding="utf-8")


class Plans:
    def __init__(self, ws: Workspace, *, background: bool = True) -> None:
        self.ws = ws
        self.background = background
        self._lock = threading.Lock()

    # reading ------------------------------------------------------------------------------

    def get(self, project: str = "", conversation: str = "") -> Plan | None:
        """The plan of a conversation, or (no conversation) the project's own plan."""
        repo = self.ws.orch(project).repo
        path = plan_file(repo, conversation)
        if path.is_file():
            return Plan.load(repo, conversation)
        if conversation and (repo / PLAN_PATH).is_file():
            # Before C14 a chat's plan was the project's plan.yaml: move it to its chat.
            old = Plan.load(repo)
            if old.conversation == conversation:
                old.save(repo, conversation)
                (repo / PLAN_PATH).unlink(missing_ok=True)
                return old
        return None

    def all(self, project: str = "") -> list[tuple[str, Plan]]:
        """Every plan in the project: ("", the project's plan) first, then one per chat."""
        repo = self.ws.orch(project).repo
        found: list[tuple[str, Plan]] = []
        if (repo / PLAN_PATH).is_file():
            found.append(("", Plan.load(repo)))
        folder = repo / PLANS_DIR
        for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
            try:
                found.append((path.stem, Plan.load(repo, path.stem)))
            except (ValueError, OSError):
                continue  # a half-edited or foreign file isn't a plan
        return found

    def latest(self, project: str = "", by: str | None = None) -> tuple[str, Plan] | None:
        """The plan someone is most likely talking about: the newest one that isn't done
        (`by`: only plans that person made)."""
        live = [
            (k, p) for k, p in self.all(project)
            if p.state != "done" and self.steps(p) and (by is None or p.by == by)
        ]  # fmt: skip
        if not live:
            return None
        rank = {"running": 2, "paused": 1, "draft": 0}
        return max(live, key=lambda kp: (rank.get(kp[1].state, 0), kp[1].updated))

    def _save(self, project: str, key: str, plan: Plan) -> None:
        repo = self.ws.orch(project).repo
        plan.save(repo, key)
        if key and plan.size == "major":
            write_docs(repo, key, plan)

    @staticmethod
    def steps(plan: Plan) -> list[Task]:
        return [t for phase in plan.phases for t in phase.tasks]

    def summary(self, project: str = "", conversation: str = "") -> str:
        """The plan in a few lines, for the chat model's prompt ("" when there is none)."""
        plan = self.get(project, conversation)
        if plan is None or not self.steps(plan):
            return ""
        marks = {"done": "x", "active": ">", "needs_you": "?", "blocked": "!", "queued": " ",
                 "skipped": "-"}  # fmt: skip
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
        request: str = "",
        size: PlanSize = "",
        spec: str = "",
        phases: list[tuple[str, str, list[str]]] | None = None,
    ) -> Plan:
        """Replace the conversation's plan (or, with no conversation, the project's) with a new
        draft. Steps already done are history: a new draft only replaces a plan that is a
        draft, paused, or done. `phases` (title, done-when, steps) makes a phased plan; without
        it the steps are one phase."""
        if phases:
            phases = [(t, g, [s.strip() for s in st if s.strip()]) for t, g, st in phases]
            phases = [p for p in phases if p[2]][:MAX_PHASES]
            budget, kept = MAX_MAJOR_STEPS, []
            for title, gate, st in phases:
                kept.append((title, gate, st[:budget]))
                budget -= len(kept[-1][2])
                if budget <= 0:
                    break
            phases = [p for p in kept if p[2]]
        steps = [s.strip() for s in steps if s.strip()][:MAX_STEPS]
        if not steps and not phases:
            raise ValueError("a plan needs at least one step")
        name = project or self.ws.default
        with self._lock:
            old = self.get(name, conversation)
            if old is not None and old.state == "running":
                raise PlanBusy("the plan is building: pause it (reject the step) to change it")
            if phases and len(phases) > 1:
                built = [
                    Phase(id=n, title=title or f"Phase {n}", gate=gate or "Every step approved",
                          tasks=[Task(id=f"{n}.{i}", title=title_of(s), detail=s)
                                 for i, s in enumerate(st, start=1)])
                    for n, (title, gate, st) in enumerate(phases, start=1)
                ]  # fmt: skip
            else:
                flat = phases[0][2] if phases else steps
                tasks = [
                    Task(id=str(i), title=title_of(s), detail=s)
                    for i, s in enumerate(flat[: MAX_MAJOR_STEPS if phases else MAX_STEPS], 1)
                ]
                built = [Phase(id=1, title="Build", gate="Every step approved", tasks=tasks)]
            plan = Plan(
                project=name,
                goal=goal.strip(),
                phases=built,
                lane=lane,
                by=by,
                conversation=conversation,
                think=think,
                request=request.strip()[:4000],
                size=size,
                spec=spec.strip() or (old.spec if old else ""),
            )
            self._save(name, conversation, plan)
            return plan

    def discard(self, project: str = "", conversation: str = "", *, force: bool = False) -> bool:
        """Throw a conversation's plan away (Start over, or the chat was deleted)."""
        if not conversation:
            raise ValueError("only a conversation's plan can be discarded here")
        repo = self.ws.orch(project).repo
        with self._lock:
            plan = self.get(project, conversation)
            if plan is None:
                return False
            if plan.state == "running" and not force:
                raise PlanBusy("the plan is building: pause it (reject the step) first")
            plan_file(repo, conversation).unlink(missing_ok=True)
            shutil.rmtree(repo / PLANS_DIR / conversation, ignore_errors=True)
            return True

    # building -----------------------------------------------------------------------------

    def go(self, project: str = "", *, by: str = "", conversation: str = "") -> Plan:
        """Start (or resume) building: the next unfinished step runs."""
        name = project or self.ws.default
        with self._lock:
            plan = self.get(name, conversation)
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
            self._save(name, conversation, plan)
        self._next(name, conversation)
        result = self.get(name, conversation)
        assert result is not None
        return result

    def _next(self, project: str, key: str = "") -> None:
        with self._lock:
            plan = self.get(project, key)
            if plan is None or plan.state != "running":
                return
            step = next((t for t in self.steps(plan) if t.status == "queued"), None)
            if step is None:
                plan.state = "done"
                self._save(project, key, plan)
                return
            step.status = "active"
            self._save(project, key, plan)
            task = self._task_for(plan, step)
        work = threading.Thread(
            target=self._run_step, args=(project, key, step.id, task),
            name=f"plan-{project}-{key or 'project'}-{step.id}", daemon=True,
        )  # fmt: skip
        if self.background:
            work.start()
        else:
            work.run()

    def _task_for(self, plan: Plan, step: Task) -> str:
        done = [t for t in self.steps(plan) if t.status == "done"]
        lines = [step.detail or step.title, "", f"This is step {step.id} of a plan: {plan.goal}"]
        if len(plan.phases) > 1:
            phase = next(p for p in plan.phases if step in p.tasks)
            lines.append(f"It's in phase {phase.id}, {phase.title} (done when: {phase.gate}).")
        if plan.request:
            # The step was written by the planner; the request is what the user actually asked
            # for, with the names and signatures every step must keep.
            lines.append(f"The user's request (keep its names and behavior): {plan.request}")
        if done:
            lines.append("Already done and in the repo: " + "; ".join(t.title for t in done))
        skipped = [t.title for t in self.steps(plan) if t.status == "skipped"]
        if skipped:
            lines.append("Skipped by the user (not in the repo): " + "; ".join(skipped))
        if step.notes:
            lines.append("The last try at this step went wrong. Fix this, and keep to the step:\n"
                         + step.notes)  # fmt: skip
        later = [t.title for t in self.steps(plan) if t.status == "queued" and t.id != step.id]
        if later:
            lines.append("Later steps (don't do these now): " + "; ".join(later))
        return "\n".join(lines)

    def _run_step(self, project: str, key: str, step_id: str, task: str) -> None:
        plan = self.get(project, key)
        lane = plan.lane if plan else "auto"
        # The Advisor's plan, or an unfiltered project: every step on the one model, alone.
        alone = lane == ADVISOR or self.ws.project(project).mode == "unfiltered"
        orch: Orchestrator = self.ws.advisor_orch(project) if alone else self.ws.orch(project)
        target, retry = (None, "now") if lane == "auto" else (lane, "off")
        if alone:
            target, retry = "coder", "off"
        try:
            run = orch.start(task, lane=target, task_id=step_id, by=plan.by if plan else "",
                             retry=retry, think=bool(plan and plan.think))  # fmt: skip
        except Exception:
            self._mark(project, key, step_id, "blocked", None, pause=True)
            return
        if run.state in ("ready", "needs_you"):
            self._mark(project, key, step_id, "needs_you", run.id)
        else:  # failed: the retry (if any) is in the same group, but the plan waits for you
            self._mark(project, key, step_id, "blocked", run.id, pause=True)

    def _mark(
        self,
        project: str,
        key: str,
        step_id: str,
        status: str,
        run_id: str | None,
        *,
        pause: bool = False,
    ) -> None:
        with self._lock:
            plan = self.get(project, key)
            if plan is None:
                return
            step = plan.task(step_id)
            step.status = status  # type: ignore[assignment]
            step.run = run_id
            if pause:
                plan.state = "paused"
            self._save(project, key, plan)

    def step_for(self, run: Run) -> tuple[str, Plan, Task] | None:
        """The plan step a run belongs to (with its Hybrid retries), and that plan's key."""
        project = run.project or self.ws.default
        try:
            plans = self.all(project)
        except KeyError:
            return None
        if not plans:
            return None
        orch = self.ws.orch(project)
        mine = {run.id, run.retried_from or ""}
        if run.group:  # a Hybrid retry shares its group with the run the step started
            mine |= {r.id for r in orch.recent(200) if r.group == run.group}
        mine.discard("")
        return next(
            ((key, plan, t) for key, plan in plans for t in self.steps(plan) if t.run in mine),
            None,
        )

    def _family(self, project: str, run_id: str) -> list[Run]:
        """A run and its Hybrid partner(s), still waiting for a decision."""
        orch = self.ws.orch(project)
        try:
            first = orch.get(run_id)
        except KeyError:
            return []
        family = [first] + [
            r
            for r in orch.recent(200)
            if first.group and r.group == first.group and r.id != first.id
        ]
        return [r for r in family if r.state in ("ready", "needs_you", "running")]

    def retry_step(self, project: str, key: str, step_id: str, *, by: str = "",
                   feedback: str = "") -> Plan:  # fmt: skip
        """Throw the step's change away and build the step again. `feedback` (Fix it): what went
        wrong, which the next try sees."""
        name = project or self.ws.default
        with self._lock:
            plan = self.get(name, key)
            if plan is None:
                raise KeyError("there's no plan here")
            step = plan.task(step_id)
            if step.status not in ("needs_you", "blocked"):
                raise PlanBusy(
                    f"step {step.id} is {step.status.replace('_', ' ')}: nothing to retry"
                )
            busy = next((t for t in self.steps(plan) if t.status == "active"), None)
            if busy is not None:
                raise PlanBusy(f"step {busy.id} is building: wait for it first")
            old = step.run
            step.status, step.run, step.notes = "queued", None, feedback.strip()[:6000]
            plan.state = "running"
            plan.by = by or plan.by
            self._save(name, key, plan)
        for r in self._family(name, old) if old else []:
            if r.state != "running":
                self.ws.for_run(r.id).reject(r.id)  # its step is queued again: not a "no"
        self._next(name, key)
        result = self.get(name, key)
        assert result is not None
        return result

    def skip_step(self, project: str, key: str, step_id: str, *, by: str = "") -> Plan:
        """Move on without this step: its change (if any) is thrown away, the next one starts."""
        name = project or self.ws.default
        with self._lock:
            plan = self.get(name, key)
            if plan is None:
                raise KeyError("there's no plan here")
            step = plan.task(step_id)
            if step.status in ("done", "skipped", "active"):
                word = "building" if step.status == "active" else step.status
                raise PlanBusy(f"step {step.id} is {word}: it can't be skipped now")
            old = step.run
            step.status = "skipped"
            if plan.state != "draft":
                plan.state = "running"
            plan.by = by or plan.by
            self._save(name, key, plan)
        for r in self._family(name, old) if old else []:
            if r.state != "running":
                self.ws.for_run(r.id).reject(r.id)
        if plan.state == "running":
            self._next(name, key)
        result = self.get(name, key)
        assert result is not None
        return result

    def after(self, run: Run) -> None:
        """A run was approved or rejected: move its plan along. Runs outside a plan: nothing."""
        found = self.step_for(run)
        if found is None or found[2].status != "needs_you":
            return
        key, _, step = found
        project = run.project or self.ws.default
        orch = self.ws.orch(project)
        mine = {run.id, run.retried_from or ""}
        if run.group:
            mine |= {r.id for r in orch.recent(200) if r.group == run.group}
        if run.state in ("approved", "approved_partial"):
            self._mark(project, key, step.id, "done", run.id)
            self._next(project, key)
        elif run.state == "rejected":
            # The other run of a Hybrid pair is rejected when you approve one: that's not a no.
            if any(r.state.startswith("approved") for r in orch.recent(200) if r.id in mine):
                return
            self._mark(project, key, step.id, "blocked", run.id, pause=True)
