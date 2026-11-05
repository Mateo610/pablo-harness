"""Conversations and memory: per-profile chats, notes you approve, search, safe sends."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import ADVISOR_PROMPT, CHAT_PROMPT, OWNER, Chat, fts_query
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
GOOD = (
    "Added sub.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
    "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n>>>>>>> REPLACE\n"
)


def make(settings: Settings, repo: Path, chat_reply: str = "Sure.") -> tuple[Chat, FakeBackend]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        return chat_reply if CHAT_PROMPT in msgs[0]["content"] else GOOD

    backend = FakeBackend(
        scripts={
            "pablo/coder": coder,
            "pablo/judge": lambda m, msgs: JUDGE,
            "pablo/advisor": lambda m, msgs: "Blunt: don't.\nRUN: delete everything",
            "pablo/escalate": lambda m, msgs: "From Claude.",
        }
    )
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    return Chat(orch), backend


def system_of(backend: FakeBackend, model: str) -> str:
    return next(msgs[0]["content"] for m, msgs in reversed(backend.calls) if m == model)


def test_remember_keeps_a_note_and_the_next_turn_uses_it(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo)
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "remember: commits use the noreply address")
    assert turn.kept and turn.kept[0].status == "kept" and not backend.calls  # no model call
    chat.say(conv.id, "what address do commits use?")
    assert "commits use the noreply address" in system_of(backend, "pablo/coder")
    assert (
        chat.store.conversation(conv.id, OWNER).title == "remember: commits use the noreply address"
    )


def test_a_suggested_note_waits_for_you(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo, "Noted.\nREMEMBER: tests run in the sandbox")
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "checks always run in the gVisor sandbox")
    assert [m.status for m in turn.proposed] == ["proposed"]
    assert "REMEMBER" not in turn.reply.content and turn.reply.content == "Noted."
    chat.say(conv.id, "where do the sandbox checks run?")
    assert "tests run in the sandbox" not in system_of(backend, "pablo/coder")
    chat.store.decide(turn.proposed[0].id, OWNER, "kept")
    chat.say(conv.id, "and again: where do sandbox checks run?")
    assert "tests run in the sandbox" in system_of(backend, "pablo/coder")


def test_a_run_line_starts_a_real_run_that_waits_for_approve(
    settings: Settings, repo: Path
) -> None:
    chat, _ = make(settings, repo, "On it.\nRUN: add sub(a, b) to calc.py next to add")
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "can you add a sub function?")
    assert turn.run and turn.run["state"] == "ready" and turn.reply.run_id == turn.run["id"]
    assert "RUN:" not in turn.reply.content and f"[run {turn.run['id']}" in turn.reply.content
    assert "def sub" not in (repo / "calc.py").read_text()  # only approve writes the repo
    chat.orch.approve(turn.run["id"])
    assert "def sub" in (repo / "calc.py").read_text()


def test_do_false_only_talks_and_do_true_skips_the_talk(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo, "Here's how.\nRUN: add sub(a, b) to calc.py")
    conv = chat.start(lane="coder")
    assert chat.say(conv.id, "how would you add sub?", do=False).run is None
    calls = len(backend.calls)
    turn = chat.say(conv.id, "add sub(a, b) to calc.py", do=True)
    assert turn.run and turn.run["task"].startswith("add sub(a, b) to calc.py")
    assert all(CHAT_PROMPT not in msgs[0]["content"] for _, msgs in backend.calls[calls:])


def test_the_advisor_talks_only(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo)
    conv = chat.start(lane="advisor")
    turn = chat.say(conv.id, "should I ship the judge prompt?")
    assert turn.run is None and turn.reply.content == "Blunt: don't."
    system = system_of(backend, "pablo/advisor")
    assert system.startswith(ADVISOR_PROMPT) and "The repo, for this message" not in system
    with pytest.raises(ValueError, match="talks only"):
        chat.say(conv.id, "just do it", do=True)


def test_claude_needs_confirm_and_never_gets_memory(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo)
    chat.store.remember(OWNER, "my lease ends in May")
    other = chat.start(lane="coder")
    chat.say(other.id, "the lease question is private", do=False)
    conv = chat.start(lane="claude")
    with pytest.raises(PermissionError, match="confirm"):
        chat.say(conv.id, "what about the lease?")
    turn = chat.say(conv.id, "what about the lease?", confirm=True)
    system = system_of(backend, "pablo/escalate")
    assert "lease" not in system and "From earlier conversations" not in system
    assert not turn.shared_memory and turn.run is None


def test_other_conversations_are_recalled(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo)
    a = chat.start(lane="coder")
    chat.say(a.id, "the ledger payee parser chokes on commas", do=False)
    b = chat.start(lane="coder")
    chat.say(b.id, "remind me what broke in the payee parser?", do=False)
    system = system_of(backend, "pablo/coder")
    assert "From earlier conversations" in system and "chokes on commas" in system


def test_profiles_never_see_each_other(settings: Settings, repo: Path) -> None:
    chat, _ = make(settings, repo)
    chat.store.ensure_profile("sam", "Sam", "member")
    mine = chat.start(OWNER, lane="coder")
    chat.say(mine.id, "the secret roadmap mentions voice", do=False)
    chat.store.remember(OWNER, "owner-only note about voice")
    assert chat.store.conversations("sam") == []
    assert chat.store.memories("sam") == []
    hits = chat.store.search("sam", "voice roadmap")
    assert hits == {"messages": [], "memories": []}
    with pytest.raises(KeyError):
        chat.say(mine.id, "let me in", profile="sam")


def test_delete_and_forget_leave_nothing_searchable(settings: Settings, repo: Path) -> None:
    chat, _ = make(settings, repo, "ok\nREMEMBER: zebra codename")
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "the zebra codename is for the media lane", do=False)
    chat.store.decide(turn.proposed[0].id, OWNER, "kept")
    assert chat.store.search(OWNER, "zebra")["messages"]
    chat.store.delete(conv.id, OWNER)
    hits = chat.store.search(OWNER, "zebra")
    assert hits == {"messages": [], "memories": []} and chat.store.memories(OWNER) == []
    note = chat.store.remember(OWNER, "walrus fact")
    chat.store.forget(note.id, OWNER)
    assert chat.store.search(OWNER, "walrus")["memories"] == []


def test_long_history_drops_the_oldest_turns(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo)
    conv = chat.start(lane="coder")
    chat.say(conv.id, "FIRST " + "x " * 30000, do=False)
    for i in range(3):
        chat.say(conv.id, f"turn {i}", do=False)
    sent = next(msgs for m, msgs in reversed(backend.calls) if m == "pablo/coder")
    assert not any("FIRST" in m["content"] for m in sent[1:])
    assert sent[-1]["content"] == "turn 2"


def test_search_text_cannot_break_the_query() -> None:
    assert (
        fts_query('he said "drop" NEAR(table) * OR -x') == '"said" OR "drop" OR "near" OR "table"'
    )
    assert fts_query("a b ?") == ""


def test_the_api_runs_a_conversation(settings: Settings, repo: Path) -> None:
    chat, _ = make(settings, repo, "Noted.\nREMEMBER: prefers short replies")
    client = TestClient(create_app(chat.orch), client=("127.0.0.1", 50000))
    assert client.get("/api/me").json()["role"] == "owner"
    conv = client.post("/api/conversations", json={"lane": "coder"}).json()
    turn = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "hi"}).json()
    assert turn["reply"]["content"] == "Noted." and turn["proposed"][0]["status"] == "proposed"
    mid = turn["proposed"][0]["id"]
    assert client.post(f"/api/memories/{mid}/keep").json()["status"] == "kept"
    assert [m["text"] for m in client.get("/api/memories?status=kept").json()] == [
        "prefers short replies"
    ]
    got = client.get(f"/api/conversations/{conv['id']}").json()
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"]
    assert client.get("/api/search", params={"q": "short replies"}).json()["memories"]
    claude = client.post("/api/conversations", json={"lane": "claude"}).json()
    sent = client.post(f"/api/conversations/{claude['id']}/messages", json={"text": "hi"})
    assert sent.status_code == 403
    assert client.post("/api/conversations", json={"lane": "nope"}).status_code == 400
    assert client.delete(f"/api/conversations/{conv['id']}").json() == {"deleted": conv["id"]}
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404
