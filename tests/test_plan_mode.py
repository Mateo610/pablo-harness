"""C14a: Plan mode in chat (talks toward a plan, never starts a run or a build) and kept files."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat
from pablo.files import FileStore, safe_name
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.plans import Plans
from pablo.projects import Workspace

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
PLAN = "Here it is.\nPLAN: a greeter\nSTEP: create greet.py\nSTEP: create shout.py\n"


def make(settings: Settings, repo: Path) -> tuple[Chat, Plans, FakeBackend]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"].split("]\n\n")[-1].lower()
        if CHAT_PROMPT.splitlines()[0] not in msgs[0]["content"]:
            return "Done."
        if "plan" in last:
            return PLAN
        if "go" in last:
            return "Building it.\nBUILD: go"
        return "On it.\nRUN: add a --version flag"  # a model that wants to start work

    fake = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    return Chat(orch, workspace=ws, plans=plans), plans, fake


def test_plan_mode_never_starts_a_run_even_when_the_model_writes_one(
    settings: Settings, repo: Path
) -> None:
    chat, _, fake = make(settings, repo)
    conv = chat.start()
    turn = chat.say(conv.id, "add a version flag", plan="major", do=True)
    assert turn.run is None and "RUN:" not in turn.reply.content
    system = fake.calls[-1][1][0]["content"]
    assert "Plan mode (major)" in system and "no RUN line" in system


def test_plan_mode_drafts_a_plan_but_go_doesnt_build_it(settings: Settings, repo: Path) -> None:
    chat, plans, _ = make(settings, repo)
    conv = chat.start()
    drafted = chat.say(conv.id, "let's plan a greeter", plan="minor")
    assert drafted.plan and drafted.plan["state"] == "draft"
    went = chat.say(conv.id, "looks good, go", plan="minor")
    plan = plans.get()
    assert went.run is None and plan and plan.state == "draft"
    assert all(step.status == "queued" for step in Plans.steps(plan))
    assert not (repo / "greet.py").exists()


def test_auto_still_builds_on_go(settings: Settings, repo: Path) -> None:
    chat, plans, _ = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "let's plan a greeter", plan="minor")
    chat.say(conv.id, "looks good, go")  # back in Auto: go builds the reviewed plan
    plan = plans.get()
    assert plan and plan.state == "running"


def test_plan_mode_is_never_spoken(settings: Settings, repo: Path) -> None:
    chat, _, fake = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "what's next", plan="major", spoken=True)
    assert "Plan mode" not in fake.calls[-1][1][0]["content"]


def test_kept_files_belong_to_one_profile(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    mine = store.add("owner", "../../etc/notes.md", b"# spec")
    store.add("ana@github", "theirs.txt", b"x")
    assert [f.name for f in store.list("owner")] == ["notes.md"]
    assert store.path("owner", mine.id).read_bytes() == b"# spec"
    assert store.path("owner", mine.id).is_relative_to(tmp_path / "files")
    with pytest.raises(KeyError):
        store.path("ana@github", mine.id)
    store.forget("owner", mine.id)
    assert store.list("owner") == []


@pytest.mark.parametrize(
    "raw,clean",
    [("a/b/c.txt", "c.txt"), (".env", "env"), ("we ird$.md", "we ird_.md"), ("", "file")],
)
def test_safe_name(raw: str, clean: str) -> None:
    assert safe_name(raw) == clean


def test_files_api_keeps_lists_and_forgets(settings: Settings, repo: Path) -> None:
    chat_backend = FakeBackend(scripts={})
    orch = Orchestrator(settings, chat_backend, repo=repo, checks=CHECKS)
    client = TestClient(create_app(orch), client=("127.0.0.1", 50000))
    made = client.post("/api/files?name=spec.md", content=b"# the spec").json()
    assert made["name"] == "spec.md" and made["size"] == 10
    assert [f["id"] for f in client.get("/api/files").json()] == [made["id"]]
    assert client.post("/api/files?name=empty.txt", content=b"").status_code == 400
    too_big = client.post("/api/files?name=big.bin", content=b"x" * (5 * 1024 * 1024 + 1))
    assert too_big.status_code == 413
    assert client.delete(f"/api/files/{made['id']}").json() == {"forgot": made["id"]}
    assert client.get("/api/files").json() == []
    assert client.delete("/api/files/nope").status_code == 404


def test_say_api_passes_plan_mode(settings: Settings, repo: Path) -> None:
    chat, _, fake = make(settings, repo)
    client = TestClient(create_app(chat.orch), client=("127.0.0.1", 50000))
    cid = client.post("/api/conversations", json={}).json()["id"]
    turn = client.post(f"/api/conversations/{cid}/messages", json={"text": "hi", "plan": "minor"})
    assert turn.status_code == 200 and turn.json()["run"] is None
    assert any("Plan mode (minor)" in m[0]["content"] for _, m in fake.calls)
    bad = client.post(f"/api/conversations/{cid}/messages", json={"text": "hi", "plan": "huge"})
    assert bad.status_code == 422
