"""C14a/b: Plan mode in chat (talks toward a plan, never starts a run or a build), kept files,
one plan per chat, and Major plans (interview, spec, phases)."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat, show_spec
from pablo.files import FileStore, safe_name
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.plans import PlanBusy, Plans, parse_phases
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
    plan = plans.get("", conv.id)
    assert went.run is None and plan and plan.state == "draft"
    assert all(step.status == "queued" for step in Plans.steps(plan))
    assert not (repo / "greet.py").exists()


def test_auto_still_builds_on_go(settings: Settings, repo: Path) -> None:
    chat, plans, _ = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "let's plan a greeter", plan="minor")
    chat.say(conv.id, "looks good, go")  # back in Auto: go builds the reviewed plan
    plan = plans.get("", conv.id)
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


# C14b: one plan per chat --------------------------------------------------------------------


def test_two_chats_plan_and_build_side_by_side(settings: Settings, repo: Path) -> None:
    chat, plans, _ = make(settings, repo)
    a, b = chat.start(), chat.start()
    chat.say(a.id, "let's plan a greeter")
    chat.say(a.id, "looks good, go")
    drafted = chat.say(b.id, "let's plan a greeter")  # a's plan is building; b's is its own
    assert drafted.plan and drafted.plan["state"] == "draft"
    plan_a, plan_b = plans.get("", a.id), plans.get("", b.id)
    assert plan_a and plan_a.state == "running" and plan_b and plan_b.state == "draft"
    assert {k for k, _ in plans.all()} == {a.id, b.id}
    assert not (repo / ".pablo" / "plan.yaml").exists()


def test_an_old_project_plan_moves_to_the_chat_that_made_it(settings: Settings, repo: Path) -> None:
    chat, plans, _ = make(settings, repo)
    conv = chat.start()
    plans.draft("", "old", ["create old.py"], lane="auto", by="owner", conversation="")
    legacy = plans.get()
    assert legacy is not None
    legacy.conversation = conv.id
    legacy.save(repo)  # what a pre-C14 chat left behind
    moved = plans.get("", conv.id)
    assert moved and moved.goal == "old" and not (repo / ".pablo" / "plan.yaml").exists()
    other = chat.start()
    assert plans.get("", other.id) is None


def test_voice_talks_about_the_newest_live_plan(settings: Settings, repo: Path) -> None:
    chat, plans, fake = make(settings, repo)
    typed = chat.start()
    chat.say(typed.id, "let's plan a greeter")
    spoken = chat.start()
    chat.say(spoken.id, "what's left", spoken=True)
    note = fake.calls[-1][1][-1]["content"]
    assert "a greeter" in note and "create greet.py" in note
    chat.say(spoken.id, "go", spoken=True)  # building from a voice call builds that plan
    plan = plans.get("", typed.id)
    assert plan and plan.state == "running" and plans.get("", spoken.id) is None


def test_start_over_discards_a_draft_but_not_a_building_plan(
    settings: Settings, repo: Path
) -> None:
    chat, plans, _ = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "let's plan a greeter")
    assert plans.discard("", conv.id) is True and plans.get("", conv.id) is None
    chat.say(conv.id, "let's plan a greeter")
    chat.say(conv.id, "looks good, go")
    with pytest.raises(PlanBusy):
        plans.discard("", conv.id)
    assert plans.discard("", conv.id, force=True) is True


# C14b: Major plans ----------------------------------------------------------------------------

SPEC = (
    "Here's the spec.\nSPEC: Greeter\n## Goal\nGreet people.\n## Requirements\n- greet(name)\n"
    "END SPEC\nUse it, edit it, or keep refining?"
)
PHASED = (
    "PLAN: a greeter you can run\n"
    "PHASE: Core | greet() works and is tested\n"
    "STEP: create greet.py with greet(name)\nSTEP: test greet\n"
    "PHASE: CLI | python -m greet prints a greeting\n"
    "STEP: add __main__.py\n"
)


def major(settings: Settings, repo: Path) -> tuple[Chat, Plans, FakeBackend]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"].split("]\n\n")[-1].lower()
        if "use this spec" in last:
            return PHASED
        if "spec" in last:
            return SPEC
        return "Who is it for?"

    fake = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    return Chat(orch, workspace=ws, plans=plans), plans, fake


def test_a_major_plan_goes_spec_then_phases_and_keeps_its_markdown(
    settings: Settings, repo: Path
) -> None:
    chat, plans, fake = major(settings, repo)
    conv = chat.start()
    asked = chat.say(conv.id, "a greeter", plan="major")
    assert asked.plan is None and "Who is it for?" in asked.reply.content
    spec = chat.say(conv.id, "me; write the spec", plan="major")
    assert "```spec\n# Greeter" in spec.reply.content and "END SPEC" not in spec.reply.content
    drafted = chat.say(conv.id, "use this spec", plan="major")
    plan = plans.get("", conv.id)
    assert drafted.plan and plan and plan.size == "major"
    assert [p.title for p in plan.phases] == ["Core", "CLI"]
    assert [t.id for t in Plans.steps(plan)] == ["1.1", "1.2", "2.1"]
    assert "PHASE:" not in drafted.reply.content and "Phase 2: CLI" in drafted.reply.content
    docs = repo / ".pablo" / "plans" / conv.id
    assert "greet(name)" in (docs / "SPEC.md").read_text()
    assert "Done when: python -m greet" in (docs / "ROADMAP.md").read_text()
    assert "Progress: 0/3" in (docs / "STATE.md").read_text()
    system = fake.calls[-1][1][0]["content"]
    assert "Plan mode (major)" in system and "END SPEC" in system


def test_parse_phases() -> None:
    assert parse_phases("PLAN: x\nSTEP: a\nSTEP: b") is None
    assert parse_phases(PHASED) == [
        ("Core", "greet() works and is tested", ["create greet.py with greet(name)", "test greet"]),
        ("CLI", "python -m greet prints a greeting", ["add __main__.py"]),
    ]


def test_show_spec_turns_the_block_into_a_card() -> None:
    assert show_spec(SPEC).startswith("Here's the spec.\n```spec\n# Greeter\n\n## Goal")


def test_kept_files_reach_plan_mode_only(settings: Settings, repo: Path) -> None:
    chat, _, fake = make(settings, repo)
    chat.files.add("owner", "brief.md", b"The tool is called Sprout.")
    chat.files.add("owner", "logo.png", b"\x89PNG\x00\xff")
    conv = chat.start()
    chat.say(conv.id, "what is this repo?")  # a question in Auto: talks, starts nothing
    assert "Sprout" not in fake.calls[-1][1][0]["content"]
    chat.say(conv.id, "let's plan it", plan="minor")
    system = next(
        m[0]["content"] for _, m in reversed(fake.calls) if "Plan mode" in m[0]["content"]
    )
    assert "### brief.md\nThe tool is called Sprout." in system
    assert "### logo.png\n(not text: not read)" in system


def test_voice_names_a_phased_step() -> None:
    from types import SimpleNamespace

    from pablo.voice import news_line

    run = SimpleNamespace(task="add main.py\n\nThis is step 2.1 of a plan: g", state="ready",
                          certainty=0.9, question="")  # fmt: skip
    assert news_line(run).startswith("Step 2.1, add main.py,")


def test_plan_api_per_chat(settings: Settings, repo: Path) -> None:
    chat, plans, _ = make(settings, repo)
    client = TestClient(create_app(chat.orch), client=("127.0.0.1", 50000))
    cid = client.post("/api/conversations", json={}).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": "let's plan a greeter"})
    assert client.get("/api/plan").status_code == 404  # the project has no plan of its own
    got = client.get(f"/api/plan?conversation={cid}").json()
    assert got["state"] == "draft" and got["conversation"] == cid
    assert client.get("/api/plan?conversation=../../etc").status_code == 404
    assert client.delete(f"/api/plan?conversation={cid}").json() == {"discarded": True}
    client.post(f"/api/conversations/{cid}/messages", json={"text": "let's plan a greeter"})
    assert client.post(f"/api/plan/go?conversation={cid}").json()["state"] == "running"
    assert client.delete(f"/api/plan?conversation={cid}").status_code == 409  # building
    client.delete(f"/api/conversations/{cid}")
    assert plans.all() == []  # the chat's plan went with it


def test_a_long_step_keeps_its_first_words_as_its_title() -> None:
    from pablo.plans import title_of

    long = (
        "create plantwatch/reader.py with read_moisture(path) and tests for good, empty and"
        " missing files"
    )
    title = title_of(long)
    assert title == "create plantwatch/reader.py with read_moisture(path) and tests for…"
    assert title_of("short step\nmore detail") == "short step"
