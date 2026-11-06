"""Conversations and memory: per-profile chats, notes you approve, search, safe sends."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import ADVISOR_PROMPT, CHAT_PROMPT, OWNER, Chat, Store, fts_query, said_remember
from pablo.gateway import FakeBackend, Message
from pablo.lessons import Lessons
from pablo.orchestrator import Orchestrator
from pablo.overnight import JobQueue

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


def test_hybrid_retries_on_deep_now_and_a_named_model_answers_alone(
    settings: Settings, repo: Path
) -> None:
    """Hybrid is the whole system; Base (or any model you name) is that model alone."""
    unsure = "I'm not sure what to change."
    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: unsure,
                                   "pablo/deep": lambda m, x: GOOD,
                                   "pablo/judge": lambda m, x: JUDGE})  # fmt: skip
    settings.lanes["coder"].retry_when = "overnight"  # config says wait; Hybrid says now
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    orch.gpu.set_mode("interactive")
    chat = Chat(orch)

    hybrid = chat.start(lane="auto")
    turn = chat.say(hybrid.id, "add sub(a, b) to calc.py", do=True)
    run = orch.get(turn.run["id"])
    assert run.retry == "now" and run.retry_run and run.retry_job is None
    assert orch.get(run.retry_run).lane == "deep" and run.project == repo.name

    base = chat.start(lane="coder")
    alone = orch.get(chat.say(base.id, "add sub(a, b) to calc.py", do=True).run["id"])
    assert alone.retry == "off" and alone.retry_run is None and alone.retry_job is None


# ---- memory keeps its promises: private, scoped, searchable, out of the task ----------------

NOTE = "zebra-note: indent with tabs"


def test_notes_reach_the_local_coder_but_never_the_task_claude_or_the_api(
    settings: Settings, repo: Path
) -> None:
    chat, backend = make(settings, repo)
    chat.store.remember(OWNER, NOTE)
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "add sub(a, b) to calc.py", do=True)
    assert turn.run is not None
    run = chat.orch.get(turn.run["id"])

    # The task is your words only; the notes ride beside it and the local coder sees them.
    assert run.task == "add sub(a, b) to calc.py" and run.notes == [NOTE]
    coder = [msgs for m, msgs in backend.calls if m == "pablo/coder"]
    assert coder and any(NOTE in msg["content"] for msg in coder[-1])

    # No answer about the run carries them; only the run's own file does.
    assert "notes" not in turn.run and "notes" not in run.to_dict()
    saved = json.loads((settings.home / "runs" / f"{run.id}.json").read_text("utf-8"))
    assert saved["notes"] == [NOTE]
    client = TestClient(create_app(chat.orch), client=("127.0.0.1", 50000))
    assert NOTE not in client.get(f"/api/runs/{run.id}").text
    assert NOTE not in client.get("/api/runs").text

    # Escalating sends the task to Claude: never the notes.
    escalated = chat.orch.escalate(run.id)
    sent = [msgs for m, msgs in backend.calls if m == "pablo/escalate"]
    assert sent and not any(NOTE in msg["content"] for msgs in sent for msg in msgs)
    assert escalated.notes == []

    # An approved change becomes a lesson keyed by the task, not by your notes.
    chat.orch.approve(run.id)
    assert all(NOTE not in ex.task for ex in Lessons(settings.home).examples())


def test_a_night_retry_stays_yours_and_the_queue_never_shows_notes(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path)
    job = queue.add("add sub", "deep", by="sam@github", notes=[NOTE])
    assert "notes" not in job.to_dict() and job.to_dict()["by"] == "sam@github"
    (stored,) = queue.all()
    assert stored.notes == [NOTE] and stored.by == "sam@github"


def test_another_projects_notes_never_come_back_past_the_ranked_cutoff(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    for i in range(13):  # more than k=12, so notes are ranked by the message
        store.remember(OWNER, f"general note {i}", scope="profile")
    store.remember(OWNER, "parser secret for beta", scope="project:beta")
    store.remember(OWNER, "parser rule for alpha", scope="project:alpha")
    picked = store.relevant_memories(OWNER, "alpha", "how does the parser work?")
    assert "parser rule for alpha" in picked
    assert "parser secret for beta" not in picked
    assert len(picked) == 12


def test_recall_stays_in_the_conversations_project(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    here = store.create(OWNER, "here", project="alpha")
    there = store.create(OWNER, "there", project="beta")
    now = store.create(OWNER, "now", project="alpha")
    store.add_message(here.id, "user", "the payee parser chokes on commas")
    store.add_message(there.id, "user", "the payee parser in beta is private")
    hits = store.recall(OWNER, "payee parser", exclude=now.id, project="alpha")
    assert [h["conversation_id"] for h in hits] == [here.id]
    # The search box still finds everything that's yours.
    assert len(store.search(OWNER, "payee parser")["messages"]) == 2


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("remember: commits use the noreply address", ("commits use the noreply address", False)),
        ("Remember that I use uv.", ("I use uv", False)),
        ("remember for this project: tests run with -x", ("tests run with -x", True)),
        ("remember (project): deploys go through Pablo", ("deploys go through Pablo", True)),
        ("Remember that bug we had with the parser? Is it back?", None),
        ("remember when we fixed the parser", None),
        ("remember that\nthe build broke", None),
        ("remember:", None),
    ],
)
def test_what_counts_as_asking_to_remember(text: str, want: tuple[str, bool] | None) -> None:
    assert said_remember(text) == want


def test_a_question_about_the_past_reaches_the_model(settings: Settings, repo: Path) -> None:
    chat, backend = make(settings, repo, "It was the comma handling.")
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "Remember that bug we had with the parser? Is it back?", do=False)
    assert not turn.kept and turn.reply.content == "It was the comma handling."
    assert chat.store.memories(OWNER) == [] and backend.calls


def test_project_notes_from_chat_get_the_project_scope(settings: Settings, repo: Path) -> None:
    chat, _ = make(
        settings, repo, "Noted.\nREMEMBER (project): uses tabs\nREMEMBER: likes short replies"
    )
    conv = chat.start(lane="coder")
    kept = chat.say(conv.id, "remember for this project: the ledger uses cents").kept
    assert [(m.text, m.scope) for m in kept] == [("the ledger uses cents", f"project:{repo.name}")]
    proposed = chat.say(
        conv.id, "I always use tabs here, and keep replies short", do=False
    ).proposed
    assert [(m.text, m.scope) for m in proposed] == [
        ("uses tabs", f"project:{repo.name}"),
        ("likes short replies", "profile"),
    ]


def test_a_note_and_its_index_row_change_together(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    with pytest.raises(sqlite3.Error):
        store._tx(
            ("insert into memories values (?, ?, ?, ?, ?, ?, ?, ?)",
             ("m1", OWNER, "profile", "walrus", "kept", None, "t", "t")),
            ("insert into no_such_table values (1)", ()),
        )  # fmt: skip
    assert store.memories(OWNER) == []  # the first write rolled back with the second


def test_a_lost_index_row_heals_on_open_and_reindex_counts(tmp_path: Path) -> None:
    path = tmp_path / "pablo.db"
    store = Store(path)
    store.ensure_profile(OWNER, "Owner", "owner")
    kept = store.remember(OWNER, "walrus fact")
    store.remember(OWNER, "maybe later", status="proposed")
    store._q("delete from memories_fts where id = ?", (kept.id,))  # what a crash used to leave
    assert store.search(OWNER, "walrus")["memories"] == []
    again = Store(path)
    assert [m["id"] for m in again.search(OWNER, "walrus")["memories"]] == [kept.id]
    assert again.reindex() == 1  # kept notes only, once each


def test_deleting_a_conversation_takes_its_notes_in_one_go(settings: Settings, repo: Path) -> None:
    chat, _ = make(settings, repo, "ok\nREMEMBER: zebra codename")
    conv = chat.start(lane="coder")
    turn = chat.say(conv.id, "the zebra codename is for the media lane", do=False)
    chat.store.decide(turn.proposed[0].id, OWNER, "kept")
    chat.store.delete(conv.id, OWNER)
    assert chat.store.memories(OWNER) == [] and chat.store.reindex() == 0


def test_the_stable_layout_keeps_the_system_prompt_the_same_turn_to_turn(
    settings: Settings, repo: Path
) -> None:
    settings.chat.stable_prefix = True
    chat, backend = make(settings, repo)
    chat.store.remember(OWNER, NOTE)
    earlier = chat.start(lane="coder")
    chat.say(earlier.id, "the payee parser chokes on commas", do=False)
    conv = chat.start(lane="coder")
    chat.say(conv.id, "what does calc.py do?", do=False)
    first = system_of(backend, "pablo/coder")
    chat.say(conv.id, "remind me what broke in the payee parser?", do=False)
    second = system_of(backend, "pablo/coder")
    assert first == second and NOTE in first
    assert "The repo, for this message" not in first and "From earlier" not in first
    last = next(msgs for m, msgs in reversed(backend.calls) if m == "pablo/coder")[-1]
    assert last["content"].startswith("[Pablo, not the user:")
    assert "From earlier conversations" in last["content"] and "chokes on commas" in last["content"]
    assert last["content"].endswith("remind me what broke in the payee parser?")


def test_recall_brings_back_your_words_not_the_models(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    old = store.create(OWNER, "old", project="alpha")
    now = store.create(OWNER, "now", project="alpha")
    store.add_message(old.id, "user", "why does the payee parser drop rows?")
    store.add_message(old.id, "assistant", "The payee parser drops rows with quoted commas.")
    hits = store.recall(OWNER, "payee parser rows", exclude=now.id, project="alpha")
    assert [h["role"] for h in hits] == ["user"]


def test_recall_needs_two_shared_words(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    old = store.create(OWNER, "old", project="alpha")
    now = store.create(OWNER, "now", project="alpha")
    store.add_message(old.id, "user", "rename the file for the voice server")
    assert store.recall(OWNER, "which file holds the judge prompt?", exclude=now.id,
                        project="alpha") == []  # fmt: skip
    assert store.recall(OWNER, "the voice server file", exclude=now.id, project="alpha")
    assert store.recall(OWNER, "voice", exclude=now.id, project="alpha")  # one word asked


def test_recall_prefers_recent_over_old_at_the_same_match(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    a = store.create(OWNER, "a", project="alpha")
    b = store.create(OWNER, "b", project="alpha")
    now = store.create(OWNER, "now", project="alpha")
    store.add_message(a.id, "user", "the payee parser chokes on commas")
    store.add_message(b.id, "user", "the payee parser chokes on tabs")
    old = "2026-01-01T00:00:00+00:00"
    store._q("update messages set created_at = ? where conversation_id = ?", (old, a.id))
    hits = store.recall(OWNER, "payee parser chokes", exclude=now.id, project="alpha",
                        now=datetime(2026, 11, 6, tzinfo=UTC))  # fmt: skip
    assert [h["conversation_id"] for h in hits] == [b.id, a.id]


def test_the_same_note_is_never_kept_or_suggested_twice(settings: Settings, repo: Path) -> None:
    chat, _ = make(settings, repo, "Noted.\nREMEMBER: Tests run in the sandbox.")
    conv = chat.start(lane="coder")
    first = chat.say(conv.id, "checks run in the gVisor sandbox", do=False).proposed
    again = chat.say(conv.id, "yes, the sandbox", do=False).proposed
    assert len(first) == 1 and again == []  # already waiting
    chat.store.decide(first[0].id, OWNER, "rejected")
    assert chat.say(conv.id, "the sandbox, again", do=False).proposed == []  # you said no
    kept = chat.say(conv.id, "remember: tests run in the sandbox").kept[0]
    assert kept.status == "kept"  # your own words win over an earlier no
    assert chat.say(conv.id, "remember: Tests run in the SANDBOX!").kept[0].id == kept.id
    assert len(chat.store.memories(OWNER, status="kept")) == 1


def test_keeping_what_was_suggested_keeps_the_suggestion(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    waiting = store.propose(OWNER, "commits use the noreply address")
    assert waiting is not None
    kept = store.keep(OWNER, "commits use the noreply address")
    assert kept.id == waiting.id and kept.status == "kept"
    assert store.propose(OWNER, "commits use the noreply address please") is None  # near-same
    assert store.propose(OWNER, "commits use the noreply address", scope="project:x") is None


def test_a_note_can_be_edited_and_search_follows(tmp_path: Path) -> None:
    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    note = store.keep(OWNER, "tests run with pytest")
    store.edit(note.id, OWNER, "tests run with unittest")
    assert [m["text"] for m in store.search(OWNER, "unittest")["memories"]] == [
        "tests run with unittest"
    ]
    assert store.search(OWNER, "pytest")["memories"] == []
    with pytest.raises(ValueError):
        store.edit(note.id, OWNER, "  ")
    with pytest.raises(KeyError):
        store.edit(note.id, "sam", "mine now")


def test_old_suggestions_are_set_aside(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    store = Store(tmp_path / "pablo.db")
    store.ensure_profile(OWNER, "Owner", "owner")
    old = store.propose(OWNER, "likes long replies")
    fresh = store.propose(OWNER, "uses uv for installs")
    assert old is not None and fresh is not None
    store._q("update memories set created_at = '2026-01-01T00:00:00+00:00' where id = ?",
             (old.id,))  # fmt: skip
    assert store.expire_proposals(now=datetime(2026, 11, 6, tzinfo=UTC)) == 1
    assert [m.id for m in store.memories(OWNER, status="proposed")] == [fresh.id]
    assert store.propose(OWNER, "likes long replies") is None  # set aside counts as a no


def test_the_api_edits_a_note(settings: Settings, repo: Path) -> None:
    chat, _ = make(settings, repo)
    client = TestClient(create_app(chat.orch), client=("127.0.0.1", 50000))
    note = client.post("/api/memories", json={"text": "uses pytest"}).json()
    assert client.post("/api/memories", json={"text": "Uses pytest."}).json()["id"] == note["id"]
    edited = client.patch(f"/api/memories/{note['id']}", json={"text": "uses unittest"})
    assert edited.json()["text"] == "uses unittest"
    assert client.patch(f"/api/memories/{note['id']}", json={"text": ""}).status_code == 400
    assert client.patch("/api/memories/nope", json={"text": "x"}).status_code == 404
