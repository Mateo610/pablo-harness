"""Extended thinking: a Chat switch for Base and Hybrid that uses the coder's thinking variant."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from fastapi.testclient import TestClient
from tests.fakes import FakeBackend

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat
from pablo.gateway import Message
from pablo.orchestrator import Orchestrator
from pablo.plans import Plans
from pablo.projects import Workspace

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
EDIT = (
    "Done.\n\nhello.py\n<<<<<<< SEARCH\n=======\ndef hello():\n    return 'hi'\n>>>>>>> REPLACE\n"
)


def backend(reply: str) -> FakeBackend:
    def code(model: str, msgs: Sequence[Message]) -> str:
        return reply if CHAT_PROMPT.splitlines()[0] in msgs[0]["content"] else EDIT

    return FakeBackend(scripts={"pablo/coder": code, "pablo/coder-think": code,
                                "pablo/judge": lambda m, x: JUDGE})  # fmt: skip


def test_a_thinking_run_uses_the_variant_and_its_room(settings: Settings, repo: Path) -> None:
    assert settings.lanes["coder"].thinking is not None
    fake = backend("")
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    run = orch.start("add hello() to hello.py", lane="coder", think=True)
    assert run.think and run.model == "pablo/coder-think"
    assert {m for m, _ in fake.calls} == {"pablo/coder-think", "pablo/judge"}
    assert orch.lane_for(run).max_tokens == settings.lanes["coder"].thinking.max_tokens
    plain = orch.start("add hello() to hello.py", lane="coder")
    assert not plain.think and plain.model == "pablo/coder"
    deep = orch.begin("anything", lane="deep", think=True)
    assert not deep.think  # deep has no thinking variant: it already reasons


def test_chat_thinks_for_base_and_hybrid_only_when_asked(settings: Settings, repo: Path) -> None:
    fake = backend("On it.\nRUN: add hello() to hello.py")
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    chat = Chat(orch)
    conv = chat.start(lane="auto")
    turn = chat.say(conv.id, "add a hello function", think=True)
    assert fake.calls[0][0] == "pablo/coder-think"  # the reply thought first
    assert turn.run and turn.run["think"] and turn.run["model"] == "pablo/coder-think"
    fake.calls.clear()
    chat.say(conv.id, "add a hello function", think=True, spoken=True)
    assert fake.calls[0][0] == "pablo/coder"  # never on a voice call: too slow to talk
    fake.calls.clear()
    chat.say(conv.id, "add a hello function")
    assert fake.calls[0][0] == "pablo/coder"


def test_a_thinking_plan_thinks_on_every_step(settings: Settings, repo: Path) -> None:
    fake = backend("PLAN: greet\nSTEP: create hello.py with hello()")
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    chat = Chat(orch, workspace=ws, plans=plans)
    conv = chat.start()
    turn = chat.say(conv.id, "plan a greeter", think=True)
    assert turn.plan and turn.plan["think"]
    built = plans.go(by="owner")
    step = Plans.steps(built)[0]
    assert orch.get(step.run or "").model == "pablo/coder-think"


def test_the_api_passes_the_switch(settings: Settings, repo: Path) -> None:
    fake = backend("Sure, it says hi.")
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    c = TestClient(create_app(orch), client=("127.0.0.1", 50000))
    assert c.get("/api/lanes").json()[1]["thinking"]["model"] == "pablo/coder-think"
    conv = c.post("/api/conversations", json={"lane": "coder"}).json()
    c.post(f"/api/conversations/{conv['id']}/messages", json={"text": "hi", "think": True})
    assert fake.calls[-1][0] == "pablo/coder-think"
