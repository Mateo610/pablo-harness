"""C14c: a needs-you card can be copied, sent to chat, fixed, retried, or skipped."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat
from pablo.details import first_paragraph, run_details
from pablo.gateway import FakeBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.plans import Plans
from pablo.projects import Workspace

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
PLAN = "PLAN: a greeter\nSTEP: create greet.py\nSTEP: create shout.py\n"


def setup(settings: Settings, repo: Path) -> tuple[TestClient, Chat, Plans, FakeBackend]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        if CHAT_PROMPT.splitlines()[0] in msgs[0]["content"]:
            last = msgs[-1]["content"].split("]\n\n")[-1].lower()
            return PLAN if "plan" in last else "On it.\nRUN: add a --version flag"
        return "I couldn't see how to do that."  # no change: the run needs you

    fake = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    chat = Chat(orch, workspace=ws, plans=plans)
    client = TestClient(create_app(orch, workspace=ws), client=("127.0.0.1", 50000))
    return client, chat, plans, fake


def building(chat: Chat, plans: Plans) -> tuple[str, str]:
    conv = chat.start()
    chat.say(conv.id, "let's plan a greeter", plan="minor")
    plan = plans.go("", conversation=conv.id, by="owner")
    run = Plans.steps(plan)[0].run
    assert run and Plans.steps(plan)[0].status in ("needs_you", "blocked")
    return conv.id, run


def test_details_are_the_task_the_why_and_the_failing_output() -> None:
    run = SimpleNamespace(
        id="abc123", state="needs_you", lane="coder", project="greeter", diff="x",
        task="add a version flag\n\nThis is step 1 of a plan: g", certainty=0.41,
        question="The tests fail after the change.", judge_summary="",
        verdict={"applied": True, "files": ["cli.py"], "added": 4, "removed": 1, "checks": [
            {"name": "lint", "passed": True},
            {"name": "tests", "passed": False, "status": "made worse",
             "output_tail": "\n".join(f"line {i}" for i in range(40))},
        ]},
    )  # fmt: skip
    text = run_details(run, plan_goal="a greeter", step=SimpleNamespace(id="1", title="add it"))
    assert text.startswith("Pablo run abc123 · needs you · coder · project greeter\n")
    assert "Plan: a greeter · step 1: add it" in text and "This is step" not in text
    assert "What happened:\nThe tests fail after the change." in text
    assert "Checks that passed: lint" in text and "Check tests: made worse" in text
    assert "line 39" in text and "line 19\n" not in text  # the last 20 lines only
    assert "Files: cli.py (+4 −1)" in text and "Judge certainty: 0.41" in text
    assert first_paragraph("do it\n\nnotes") == "do it"


def test_fix_it_retries_the_plan_step_and_tells_it_what_went_wrong(
    settings: Settings, repo: Path
) -> None:
    client, chat, plans, fake = setup(settings, repo)
    cid, first = building(chat, plans)
    got = client.get(f"/api/runs/{first}/details").json()
    assert got["plan"]["conversation"] == cid and got["plan"]["step"] == "1"
    assert "Plan: a greeter · step 1" in got["text"]
    fixed = client.post(f"/api/runs/{first}/retry", json={"fix": True}).json()
    assert fixed["run"] is None
    step = fixed["plan"]["phases"][0]["tasks"][0]
    assert step["run"] and step["run"] != first and step["status"] in ("needs_you", "blocked")
    task = next(
        m[-1]["content"] for _, m in reversed(fake.calls) if "step 1 of a plan" in m[-1]["content"]
    )
    assert "The last try at this step went wrong" in task and f"Pablo run {first}" in task
    assert chat.orch.get(first).state == "rejected"


def test_retry_without_fix_sends_no_feedback(settings: Settings, repo: Path) -> None:
    client, chat, plans, fake = setup(settings, repo)
    _, first = building(chat, plans)
    client.post(f"/api/runs/{first}/retry", json={})
    task = next(
        m[-1]["content"] for _, m in reversed(fake.calls) if "step 1 of a plan" in m[-1]["content"]
    )
    assert "went wrong" not in task


def test_skip_moves_the_plan_on(settings: Settings, repo: Path) -> None:
    client, chat, plans, _ = setup(settings, repo)
    cid, first = building(chat, plans)
    skipped = client.post(f"/api/runs/{first}/skip").json()["plan"]
    one, two = skipped["phases"][0]["tasks"]
    assert one["status"] == "skipped" and two["status"] in ("needs_you", "blocked") and two["run"]
    assert chat.orch.get(first).state == "rejected"
    task = chat.orch.get(two["run"]).task
    assert "Skipped by the user (not in the repo): create greet.py" in task
    assert client.post(f"/api/runs/{first}/skip").status_code == 409  # already skipped
    plan = plans.get("", cid)
    assert plan is not None and plan.phases[0].progress() == (1, 2)


def test_a_run_outside_a_plan_starts_again_beside_it(settings: Settings, repo: Path) -> None:
    client, chat, _, _ = setup(settings, repo)
    conv = chat.start()
    turn = chat.say(conv.id, "add a version flag")
    first = turn.run["id"] if turn.run else ""
    assert first and chat.orch.get(first).state == "needs_you"
    assert client.post(f"/api/runs/{first}/skip").status_code == 400  # not a plan step
    again = client.post(f"/api/runs/{first}/retry", json={"fix": True}).json()["run"]
    assert again["id"] != first and again["task"].startswith("add a --version flag")
    assert "The last try went wrong. Fix this:" in again["task"]
    assert chat.orch.get(first).state == "rejected"
    assert client.post("/api/runs/nope/retry", json={}).status_code == 404
