from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.demo import demo_backend
from pablo.orchestrator import Orchestrator
from pablo.plan import starter_plan


def client_for(settings: Settings, repo: Path) -> TestClient:
    orch = Orchestrator(
        settings,
        demo_backend(),
        repo=repo,
        checks={"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]},
    )
    return TestClient(create_app(orch))


def test_health_and_lanes(settings: Settings, repo: Path) -> None:
    client = client_for(settings, repo)
    assert client.get("/api/health").json()["ok"] is True
    names = {lane["name"] for lane in client.get("/api/lanes").json()}
    assert {"router", "coder", "judge", "claude"} <= names


def test_plan_endpoints(settings: Settings, repo: Path) -> None:
    client = client_for(settings, repo)
    assert client.get("/api/plan").status_code == 404
    starter_plan("proj").save(repo)
    assert client.get("/api/plan").json()["project"] == "proj"
    assert client.post("/api/plan/tasks/1.1", json={"status": "done"}).json()["status"] == "done"


def test_run_inbox_approve(settings: Settings, repo: Path) -> None:
    client = client_for(settings, repo)
    run = client.post("/api/runs", json={"task": "add a demo note", "lane": "coder"}).json()
    assert run["state"] == "needs_you"  # demo judge scores 0.62
    inbox = client.get("/api/inbox").json()
    assert [r["id"] for r in inbox] == [run["id"]]
    approved = client.post(f"/api/runs/{run['id']}/approve").json()
    assert approved["state"] == "approved"
    assert (repo / "PABLO_DEMO.md").is_file()
    assert client.post(f"/api/runs/{run['id']}/approve").status_code == 409


def test_claude_lane_forbidden_without_escalate(settings: Settings, repo: Path) -> None:
    client = client_for(settings, repo)
    assert client.post("/api/runs", json={"task": "x", "lane": "claude"}).status_code == 403


def test_recent_runs_and_voice_stub(settings: Settings, repo: Path) -> None:
    client = client_for(settings, repo)
    first = client.post("/api/runs", json={"task": "one", "lane": "coder"}).json()
    client.post(f"/api/runs/{first['id']}/reject")
    second = client.post("/api/runs", json={"task": "two", "lane": "coder"}).json()
    listed = client.get("/api/runs", params={"limit": 5}).json()
    assert {r["id"] for r in listed} == {first["id"], second["id"]}
    assert client.post("/api/voice", content=b"audio").status_code == 501


def test_inbox_survives_restart(settings: Settings, repo: Path) -> None:
    run = client_for(settings, repo).post("/api/runs", json={"task": "x", "lane": "coder"}).json()
    fresh = client_for(settings, repo)  # new orchestrator, same ~/.pablo
    assert [r["id"] for r in fresh.get("/api/inbox").json()] == [run["id"]]


def test_compare_events_and_hunk_approve_api(settings: Settings, repo: Path) -> None:
    client = client_for(settings, repo)
    a, b = client.post(
        "/api/compare", json={"task": "add a demo note", "lanes": ["coder", "deep"]}
    ).json()
    assert a["group"] == b["group"]
    kinds = [e["kind"] for e in client.get(f"/api/runs/{a['id']}/events").json()]
    assert kinds[0] == "route" and kinds[-1] == "finished"
    assert client.post(f"/api/runs/{a['id']}/approve", json={"hunks": ["7:7"]}).status_code == 409
    approved = client.post(f"/api/runs/{a['id']}/approve", json={"hunks": ["0:0"]}).json()
    assert approved["state"] == "approved"
    assert client.get(f"/api/runs/{b['id']}").json()["state"] == "rejected"
    assert (
        client.post("/api/compare", json={"task": "x", "lanes": ["coder", "claude"]}).status_code
        == 403
    )
    assert client.get("/api/runs/nope/events").status_code == 404
