"""Projects: start a new one, work in several, and keep unfiltered ones on one model."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.projects import Workspace, slug, unfiltered_settings

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
NEW_FILE = (
    "Started it.\n\nhello.py\n<<<<<<< SEARCH\n=======\n"
    "def hello():\n    return 'hi'\n>>>>>>> REPLACE\n"
)


def backend() -> FakeBackend:
    def code(model: str, msgs: Sequence[Message]) -> str:
        if CHAT_PROMPT in msgs[0]["content"]:
            return "On it.\nRUN: add hello() to hello.py"
        return NEW_FILE

    return FakeBackend(scripts={"pablo/coder": code, "pablo/advisor": code,
                                "pablo/judge": lambda m, x: JUDGE})  # fmt: skip


def workspace(settings: Settings, repo: Path) -> tuple[Workspace, Orchestrator, FakeBackend]:
    fake = backend()
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    return Workspace(orch), orch, fake


def test_a_new_project_is_a_git_repo_with_what_its_about(settings: Settings, repo: Path) -> None:
    ws, _, _ = workspace(settings, repo)
    made = ws.create("Plant Watcher!", "Reads soil sensors and texts me when to water.")
    path = Path(made.path)
    assert made.name == "plant-watcher" and path.parent == settings.home / "projects"
    assert "texts me when to water" in (path / "README.md").read_text()
    log = subprocess.run(["git", "log", "--oneline"], cwd=path, capture_output=True, text=True)
    assert "Start Plant Watcher!" in log.stdout
    assert [p.name for p in ws.projects()] == [repo.name, "plant-watcher"]
    assert ws.projects()[0].default and not ws.projects()[1].default
    with pytest.raises(FileExistsError):
        ws.create("plant watcher")
    with pytest.raises(ValueError):
        slug("!!!")


def test_runs_change_their_own_project_and_approve_lands_there(
    settings: Settings, repo: Path
) -> None:
    ws, base, _ = workspace(settings, repo)
    made = ws.create("Greeter")
    chat = Chat(base, workspace=ws)
    conv = chat.start(project="greeter")
    turn = chat.say(conv.id, "make a hello function")
    run_id = turn.run["id"] if turn.run else ""
    target = ws.for_run(run_id)
    assert target.repo == Path(made.path) and target.gpu is base.gpu  # one GPU for every project
    target.approve(run_id)
    assert (Path(made.path) / "hello.py").is_file() and not (repo / "hello.py").exists()


def test_an_unfiltered_project_never_leaves_its_one_model(settings: Settings, repo: Path) -> None:
    s = unfiltered_settings(settings)
    advisor = settings.lanes["advisor"].model
    assert {s.lanes[n].model for n in ("coder", "deep", "judge")} == {advisor}
    assert not any(s.lanes[n].train_ok for n in ("coder", "deep", "judge"))
    assert "claude" not in s.lanes and s.lanes["coder"].retry_on is None

    ws, base, fake = workspace(settings, repo)
    ws.create("Raw", mode="unfiltered")
    chat = Chat(base, workspace=ws)
    conv = chat.start(project="raw", lane="deep")  # asking for Best changes nothing here
    assert conv.lane == "auto"
    turn = chat.say(conv.id, "make a hello function", lane="coder")
    used = {m for m, _ in fake.calls}
    assert used == {advisor}  # the chat reply, the code and the judge: one model
    run = ws.for_run(turn.run["id"] if turn.run else "").get(turn.run["id"] if turn.run else "")
    assert run.model == advisor and run.retry == "off" and run.project == "raw"
    with pytest.raises(KeyError):
        ws.for_run(run.id).escalate(run.id)  # there is no Claude lane in an unfiltered project


def test_the_api_lists_projects_and_only_the_owner_starts_one(
    settings: Settings, repo: Path
) -> None:
    ws, base, _ = workspace(settings, repo)
    app = create_app(base, workspace=ws)
    owner = TestClient(app, client=("127.0.0.1", 50000))
    made = owner.post("/api/projects", json={"title": "Side Quest", "about": "a game"})
    assert made.status_code == 200 and made.json()["name"] == "side-quest"
    assert [p["name"] for p in owner.get("/api/projects").json()] == [repo.name, "side-quest"]
    assert owner.post("/api/projects", json={"title": "side quest"}).status_code == 409
    conv = owner.post("/api/conversations", json={"project": "side-quest"}).json()
    got = owner.get(f"/api/conversations/{conv['id']}").json()
    assert got["conversation"]["project"] == "side-quest" and got["unfiltered"] is False
    assert owner.post("/api/conversations", json={"project": "nope"}).status_code == 400
    assert owner.get("/api/repo?project=side-quest").json()["name"] == "side-quest"
