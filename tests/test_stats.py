"""Cockpit numbers: CPU, repo state, diff since push, and token spend by lane."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.gateway import FakeBackend
from pablo.orchestrator import Orchestrator
from pablo.resources import CpuMeter
from pablo.stats import public_remote, repo_status

GOOD = (
    "Added sub.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
    "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n>>>>>>> REPLACE\n"
)


def test_the_remote_link_never_carries_credentials() -> None:
    assert public_remote("https://x-token:ghp_secret@github.com/Mateo610/pablo-harness.git") == (
        "https://github.com/Mateo610/pablo-harness"
    )
    assert public_remote("git@github.com:Mateo610/pablo-harness.git") == (
        "https://github.com/Mateo610/pablo-harness"
    )
    assert public_remote("/srv/git/local") == ""


def test_cpu_is_measured_between_calls() -> None:
    reads = iter(["cpu  100 0 100 800 0 0 0 0\n", "cpu  150 0 150 900 0 0 0 0\n"])
    meter = CpuMeter(lambda: next(reads))
    assert meter() == 0.0 and meter() == 50.0


def test_repo_status_counts_what_isnt_pushed(repo: Path) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                   cwd=repo, check=True)  # fmt: skip
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\nX = 1\n")
    s = repo_status(repo)
    assert s["git"] and s["since_push"]["files"] == 1 and s["since_push"]["added"] == 3
    assert s["since_push"]["changed"][0]["path"] == "calc.py"


def test_stats_count_runs_and_tokens_by_lane(settings: Settings, repo: Path) -> None:
    judge = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
    backend = FakeBackend(
        scripts={"pablo/coder": lambda m, x: GOOD, "pablo/judge": lambda m, x: judge}
    )
    checks = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
    orch = Orchestrator(settings, backend, repo=repo, checks=checks)
    client = TestClient(create_app(orch), client=("127.0.0.1", 5000))
    client.post("/api/runs", json={"task": "add sub(a, b)", "lane": "coder"})
    conv = client.post("/api/conversations", json={"lane": "coder"}).json()
    client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "hi", "do": False})
    s = client.get("/api/stats").json()
    coder = s["lanes"]["coder"]
    assert s["runs"] == 1 and coder["runs"] == 1 and coder["ready"] == 1
    assert coder["tokens_in"] > 0 and coder["chat_tokens_in"] > 0
    assert client.get("/api/repo").json()["name"] == repo.name
    assert client.get("/api/resources").json()["cpu_threads"] >= 1
