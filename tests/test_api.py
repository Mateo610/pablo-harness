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
