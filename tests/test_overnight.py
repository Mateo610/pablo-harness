from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Overnight, Settings
from pablo.demo import demo_backend
from pablo.orchestrator import Orchestrator
from pablo.overnight import (
    BriefStore,
    JobQueue,
    OvernightRunner,
    Scheduler,
    build_brief,
    in_window,
    window_bounds,
)
from pablo.plan import starter_plan


def make_orch(settings: Settings, repo: Path) -> Orchestrator:
    return Orchestrator(settings, demo_backend(), repo=repo, checks={})


def test_window_same_day_and_across_midnight() -> None:
    cfg = Overnight(start="01:00", end="06:30")
    assert in_window(cfg, datetime(2026, 11, 1, 3, 0))
    assert not in_window(cfg, datetime(2026, 11, 1, 7, 0))
    late = Overnight(start="23:00", end="05:00")
    assert in_window(late, datetime(2026, 11, 1, 23, 30))
    assert in_window(late, datetime(2026, 11, 1, 4, 59))
    assert not in_window(late, datetime(2026, 11, 1, 12, 0))
    start, end = window_bounds(late, date(2026, 11, 2))
    assert (start.day, end.day) == (1, 2)


def test_bad_time_rejected() -> None:
    with pytest.raises(ValueError):
        Overnight(start="25:00")


def test_queue_persists_and_cancels(tmp_path: Path) -> None:
    q = JobQueue(tmp_path)
    a = q.add("first")
    q.add("second", lane="coder")
    assert [j.task for j in JobQueue(tmp_path).pending()] == ["first", "second"]
    q.cancel(a.id)
    assert [j.task for j in q.pending()] == ["second"]
    with pytest.raises(ValueError):
        q.cancel(a.id)


def test_runner_waits_for_window(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo)
    q = JobQueue(settings.home)
    q.add("add a demo note", lane="coder")
    runner = OvernightRunner(orch, q, Overnight(start="01:00", end="06:30"))
    assert runner.run_pending(at=datetime(2026, 11, 1, 14, 0)) == []
    done = runner.run_pending(at=datetime(2026, 11, 1, 2, 0))
    assert [j.status for j in done] == ["done"]
    assert orch.get(done[0].run_id or "").state == "needs_you"  # still waits for you


def test_queued_claude_job_fails_not_escalates(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo)
    q = JobQueue(settings.home)
    q.add("anything", lane="claude")
    [job] = OvernightRunner(orch, q, settings.overnight).run_pending(force=True)
    assert job.status == "failed" and "escalate" in job.error


def test_brief_counts_the_night(settings: Settings, repo: Path) -> None:
    starter_plan("proj").save(repo)
    orch = make_orch(settings, repo)
    q = JobQueue(settings.home)
    q.add("add a demo note", lane="coder")
    q.add("anything", lane="claude")
    OvernightRunner(orch, q, settings.overnight).run_pending(force=True)
    brief = build_brief(orch, q, datetime.now().date(), repo)
    assert brief.needs_you == 1 and brief.failed == 1
    assert brief.headline == "Overnight: 0 done, 1 needs you, 1 failed"
    assert "Phase 1" in brief.plan_line
    assert "NEEDS YOU" in brief.to_markdown()


def test_scheduler_writes_brief_once(settings: Settings, repo: Path) -> None:
    orch = make_orch(settings, repo)
    q = JobQueue(settings.home)
    store = BriefStore(settings.home)
    sched = Scheduler(OvernightRunner(orch, q, settings.overnight), store, repo)
    sched.tick(datetime(2026, 11, 1, 6, 0).astimezone())
    assert store.latest() is None  # before brief_at
    sched.tick(datetime(2026, 11, 1, 7, 45).astimezone())
    first = store.latest()
    assert first is not None and first["headline"].startswith("Quiet night")
    sched.tick(datetime(2026, 11, 1, 8, 0).astimezone())
    assert len(list(store.root.glob("*.json"))) == 1


def test_queue_and_brief_api(settings: Settings, repo: Path) -> None:
    client = TestClient(create_app(make_orch(settings, repo)))
    assert client.post("/api/queue", json={"task": "x", "lane": "claude"}).status_code == 403
    job = client.post("/api/queue", json={"task": "add a demo note", "lane": "coder"}).json()
    assert [j["id"] for j in client.get("/api/queue").json()] == [job["id"]]
    ran = client.post("/api/queue/run").json()
    assert ran[0]["status"] == "done"
    assert client.get("/api/brief").status_code == 404
    made = client.post("/api/brief").json()
    assert made["needs_you"] == 1
    assert client.get("/api/brief").json()["day"] == made["day"]
    saved = json.loads((settings.home / "briefs" / f"{made['day']}.json").read_text())
    assert saved["headline"] == made["headline"]
