"""The overnight queue and the morning brief.

Slow work (the deep lane, sweeps, media) waits in a queue and runs inside a quiet
window while you sleep, one job at a time. After the window, a brief collects
what happened so the morning starts with decisions, not digging.

Nothing here writes to your repo: queued runs end in "ready" or "needs you" like
any other run, and you still approve them.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

from pablo.config import Overnight
from pablo.events import Event
from pablo.gpu import Priority
from pablo.gpu import job as gpu_job
from pablo.orchestrator import Orchestrator, Run
from pablo.plan import Plan

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


def now_local() -> datetime:
    return datetime.now().astimezone()


def _hm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_window(cfg: Overnight, at: datetime) -> bool:
    start, end, t = _hm(cfg.start), _hm(cfg.end), at.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end  # window crosses midnight


def window_bounds(cfg: Overnight, morning: date) -> tuple[datetime, datetime]:
    """The overnight window that ends on `morning` (local, naive)."""
    start, end = _hm(cfg.start), _hm(cfg.end)
    end_at = datetime.combine(morning, end)
    start_day = morning if start <= end else morning - timedelta(days=1)
    return datetime.combine(start_day, start), end_at


@dataclass
class Job:
    task: str
    lane: str | None = None
    task_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: JobStatus = "queued"
    run_id: str | None = None
    error: str = ""
    group: str | None = None  # set when this job retries a run, so approving one rejects the other
    retried_from: str | None = None
    created_at: str = field(default_factory=lambda: now_local().isoformat(timespec="seconds"))
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobQueue:
    """A JSON file of jobs. Small and inspectable on purpose."""

    def __init__(self, home: Path) -> None:
        self.path = home / "queue.json"
        self._lock = threading.Lock()

    def _read(self) -> list[Job]:
        if not self.path.is_file():
            return []
        return [Job(**row) for row in json.loads(self.path.read_text("utf-8"))]

    def _write(self, jobs: list[Job]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([j.to_dict() for j in jobs], indent=2), "utf-8")
        tmp.replace(self.path)

    def all(self) -> list[Job]:
        with self._lock:
            return self._read()

    def add(
        self,
        task: str,
        lane: str | None = None,
        task_id: str | None = None,
        *,
        group: str | None = None,
        retried_from: str | None = None,
    ) -> Job:
        job = Job(task=task, lane=lane, task_id=task_id, group=group, retried_from=retried_from)
        with self._lock:
            jobs = self._read()
            jobs.append(job)
            self._write(jobs)
        return job

    def update(self, job_id: str, **changes: Any) -> Job:
        with self._lock:
            jobs = self._read()
            for job in jobs:
                if job.id == job_id:
                    for key, value in changes.items():
                        setattr(job, key, value)
                    self._write(jobs)
                    return job
        raise KeyError(job_id)

    def cancel(self, job_id: str) -> Job:
        job = next((j for j in self.all() if j.id == job_id), None)
        if job is None:
            raise KeyError(job_id)
        if job.status != "queued":
            raise ValueError(f"job {job_id} is {job.status}")
        return self.update(job_id, status="cancelled")

    def pending(self) -> list[Job]:
        return [j for j in self.all() if j.status == "queued"]


class OvernightRunner:
    def __init__(self, orch: Orchestrator, queue: JobQueue, cfg: Overnight) -> None:
        self.orch = orch
        self.queue = queue
        self.cfg = cfg
        self._running = threading.Lock()

    def _emit(self, kind: str, job: Job, **data: Any) -> None:
        self.orch.bus.publish(Event(kind, job.run_id or job.id, {"job": job.id, **data}))

    def run_pending(self, *, force: bool = False, at: datetime | None = None) -> list[Job]:
        """Run queued jobs, one at a time, while the window is open (or now, if forced)."""
        if not self._running.acquire(blocking=False):
            return []  # already running
        finished: list[Job] = []
        try:
            for job in self.queue.pending()[: self.cfg.max_jobs]:
                if not force and not in_window(self.cfg, at or now_local()):
                    break
                lane = job.lane or (
                    self.cfg.default_lane
                    if self.cfg.default_lane in self.orch.settings.lanes
                    else None
                )
                self.queue.update(
                    job.id, status="running", started_at=now_local().isoformat(timespec="seconds")
                )
                self._emit("queue", job, status="running", text=f"tonight · {job.task[:60]}")
                try:
                    with gpu_job(Priority.NIGHT):  # yields to anyone working, even at night
                        run = self.orch.start(
                            job.task,
                            lane=lane,
                            task_id=job.task_id,
                            group=job.group,
                            retried_from=job.retried_from,
                        )
                    job = self.queue.update(
                        job.id,
                        status="done",
                        run_id=run.id,
                        finished_at=now_local().isoformat(timespec="seconds"),
                    )
                except Exception as exc:
                    job = self.queue.update(
                        job.id,
                        status="failed",
                        error=str(exc),
                        finished_at=now_local().isoformat(timespec="seconds"),
                    )
                self._emit("queue", job, status=job.status)
                finished.append(job)
        finally:
            self._running.release()
        return finished


# ---- the brief -----------------------------------------------------------------
@dataclass
class BriefItem:
    run_id: str
    task: str
    lane: str
    state: str
    certainty: float | None
    summary: str
    question: str


@dataclass
class Brief:
    day: str
    headline: str
    done: int
    needs_you: int
    failed: int
    items: list[BriefItem]
    failed_jobs: list[dict[str, str]]
    still_queued: int
    plan_line: str
    created_at: str = field(default_factory=lambda: now_local().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [f"# {self.headline}", ""]
        if self.plan_line:
            lines += [self.plan_line, ""]
        for item in self.items:
            mark = {"ready": "ready", "needs_you": "NEEDS YOU", "approved": "approved"}.get(
                item.state, item.state
            )
            cert = f" · {item.certainty:.2f}" if item.certainty is not None else ""
            lines.append(f"- **{item.task}** ({item.lane}{cert}): {mark}")
            if item.question and item.state == "needs_you":
                lines.append(f"  - {item.question}")
        for job in self.failed_jobs:
            lines.append(f"- **{job['task']}**: failed: {job['error']}")
        if self.still_queued:
            lines += ["", f"{self.still_queued} job(s) still queued for tonight."]
        return "\n".join(lines) + "\n"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def build_brief(
    orch: Orchestrator,
    queue: JobQueue,
    morning: date,
    repo: Path,
    since: datetime | None = None,
) -> Brief:
    """Everything that finished since `since` (the previous brief), else the last 24 hours."""
    cutoff = (since or now_local() - timedelta(hours=24)).astimezone()
    night_jobs = [
        j
        for j in queue.all()
        if j.finished_at and datetime.fromisoformat(j.finished_at).astimezone() > cutoff
    ]
    runs: list[Run] = []
    for job in night_jobs:
        if job.run_id:
            try:
                runs.append(orch.get(job.run_id))
            except KeyError:
                continue
    items = [
        BriefItem(
            r.id,
            r.task,
            r.lane,
            r.state,
            r.certainty,
            r.judge_summary or r.explanation[:160],
            r.question,
        )
        for r in runs
    ]
    needs = sum(i.state == "needs_you" for i in items)
    done = sum(i.state in ("ready", "approved") for i in items)
    failed_jobs = [{"task": j.task, "error": j.error} for j in night_jobs if j.status == "failed"]
    failed = len(failed_jobs) + sum(i.state == "failed" for i in items)

    if not night_jobs:
        headline = "Quiet night: nothing was queued"
    else:
        parts = [f"{done} done"]
        if needs:
            parts.append(f"{needs} need{'s' if needs == 1 else ''} you")
        if failed:
            parts.append(f"{failed} failed")
        headline = "Overnight: " + ", ".join(parts)

    plan_line = ""
    try:
        plan = Plan.load(repo)
        phase = plan.current_phase()
        if phase:
            d, total = phase.progress()
            nxt = plan.next_task()
            plan_line = f"Phase {phase.id} · {phase.title}: {d} of {total} done."
            if nxt:
                plan_line += f" Next: {nxt.id} {nxt.title}."
    except FileNotFoundError:
        pass

    return Brief(
        day=morning.isoformat(),
        headline=headline,
        done=done,
        needs_you=needs,
        failed=failed,
        items=items,
        failed_jobs=failed_jobs,
        still_queued=len(queue.pending()),
        plan_line=plan_line,
    )


class BriefStore:
    def __init__(self, home: Path) -> None:
        self.root = home / "briefs"

    def save(self, brief: Brief) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{brief.day}.md").write_text(brief.to_markdown(), "utf-8")
        path = self.root / f"{brief.day}.json"
        path.write_text(json.dumps(brief.to_dict(), indent=2), "utf-8")
        return path

    def get(self, day: str) -> dict[str, Any] | None:
        path = self.root / f"{day}.json"
        return dict(json.loads(path.read_text("utf-8"))) if path.is_file() else None

    def last_created(self) -> datetime | None:
        latest = self.latest()
        return datetime.fromisoformat(latest["created_at"]) if latest else None

    def latest(self) -> dict[str, Any] | None:
        files = sorted(self.root.glob("*.json")) if self.root.is_dir() else []
        return dict(json.loads(files[-1].read_text("utf-8"))) if files else None


class Scheduler:
    """Background tick inside `pablo serve`: run the queue in the window, write the brief after."""

    def __init__(self, runner: OvernightRunner, briefs: BriefStore, repo: Path) -> None:
        self.runner = runner
        self.briefs = briefs
        self.repo = repo
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, at: datetime | None = None) -> None:
        at = at or now_local()
        cfg = self.runner.cfg
        if in_window(cfg, at) and self.runner.queue.pending():
            self.runner.run_pending(at=at)
        if at.time() >= _hm(cfg.brief_at) and self.briefs.get(at.date().isoformat()) is None:
            brief = build_brief(
                self.runner.orch,
                self.runner.queue,
                at.date(),
                self.repo,
                since=self.briefs.last_created(),
            )
            self.briefs.save(brief)
            self.runner.orch.bus.publish(Event("brief", "brief", {"headline": brief.headline}))

    def start(self, every_s: float = 60) -> None:
        def loop() -> None:
            while not self._stop.wait(every_s):
                try:
                    self.tick()
                except Exception as exc:  # keep the scheduler alive; surface the error
                    self.runner.orch.bus.publish(Event("error", "scheduler", {"message": str(exc)}))

        self._thread = threading.Thread(target=loop, name="pablo-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
