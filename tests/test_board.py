"""C14d: the Work board shows every chat's plan, and warns when two share files."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.board import named_paths, plan_board
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.plans import Plans
from pablo.projects import Workspace

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})


def setup(settings: Settings, repo: Path) -> tuple[Chat, Plans, Workspace]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"].split("]\n\n")[-1].lower()
        if CHAT_PROMPT.splitlines()[0] not in msgs[0]["content"]:
            return "I couldn't."
        if "greeter" in last:
            return "PLAN: a greeter\nSTEP: create greet.py with greet(name)\nSTEP: test it"
        if "shouter" in last:
            return "PLAN: a shouter\nSTEP: add shout() to greet.py\nSTEP: add cli/main.py"
        return "PLAN: docs\nSTEP: write docs/usage.md"

    fake = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    return Chat(orch, workspace=ws, plans=plans), plans, ws


def test_named_paths() -> None:
    text = "add shout() to greet.py, then web/src/App.tsx and ./docs/a.md; not e.g. or v1.2"
    assert named_paths(text) == {"greet.py", "web/src/App.tsx", "docs/a.md"}


def test_two_chats_in_one_project_are_warned_about_shared_files(
    settings: Settings, repo: Path
) -> None:
    chat, plans, ws = setup(settings, repo)
    a, b, c = chat.start(), chat.start(), chat.start()
    chat.say(a.id, "plan a greeter")
    chat.say(b.id, "plan a shouter")
    chat.say(c.id, "plan the docs")
    rows = {r["conversation"]: r for r in plan_board(ws, plans, chat.store, viewer="owner",
                                                     owner=True)}  # fmt: skip
    assert set(rows) == {a.id, b.id, c.id}
    ra, rb, rc = rows[a.id], rows[b.id], rows[c.id]
    assert (
        ra["goal"] == "a greeter" and ra["state"] == "draft" and (ra["done"], ra["total"]) == (0, 2)
    )
    assert ra["current"]["id"] == "1" and ra["mine"] is True
    assert ra["overlaps"] == [{"conversation": b.id, "title": rb["title"], "files": ["greet.py"]}]
    assert rb["overlaps"][0]["conversation"] == a.id and rc["overlaps"] == []
    assert rb["planned"] == ["cli/main.py", "greet.py"]


def test_a_teammate_sees_their_own_plan_and_an_unnamed_collision(
    settings: Settings, repo: Path
) -> None:
    chat, plans, ws = setup(settings, repo)
    chat.store.ensure_profile("ana@github", "Ana")
    mine = chat.start()
    theirs = chat.start(profile="ana@github")
    chat.say(mine.id, "plan a greeter")
    chat.say(theirs.id, "plan a shouter", profile="ana@github")
    rows = plan_board(ws, plans, chat.store, viewer="ana@github", owner=False)
    assert [r["conversation"] for r in rows] == [theirs.id]
    assert rows[0]["overlaps"] == [
        {"conversation": "", "title": "another chat", "files": ["greet.py"]}
    ]
    everyone = plan_board(ws, plans, chat.store, viewer="owner", owner=True)
    assert {r["by"] for r in everyone} == {"owner", "ana@github"}


def test_finished_plans_leave_after_a_day_and_the_project_plan_stays_off(
    settings: Settings, repo: Path
) -> None:
    chat, plans, ws = setup(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "plan the docs")
    plans.draft("", "the mission board", ["x"], lane="auto", by="owner", conversation="")
    plan = plans.get("", conv.id)
    assert plan is not None
    plan.state = "done"
    plan.save(repo, conv.id)
    now = datetime.now(UTC)
    assert [r["state"] for r in plan_board(ws, plans, chat.store, viewer="owner", owner=True,
                                           now=now)] == ["done"]  # fmt: skip
    later = now + timedelta(days=2)
    assert plan_board(ws, plans, chat.store, viewer="owner", owner=True, now=later) == []


def test_the_board_api(settings: Settings, repo: Path) -> None:
    chat, _, ws = setup(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "plan a greeter")
    client = TestClient(create_app(chat.orch, workspace=ws), client=("127.0.0.1", 50000))
    rows = client.get("/api/plans").json()
    assert [r["conversation"] for r in rows] == [conv.id] and rows[0]["project"] == repo.name
