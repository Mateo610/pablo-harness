"""Plan mode: talk a plan through, say go, and each step waits for your approval."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeBackend

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT, Chat
from pablo.gateway import Message
from pablo.orchestrator import Orchestrator
from pablo.plans import PlanBusy, Plans
from pablo.projects import Workspace

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
PLAN = (
    "Here's how I'd build it.\n"
    "PLAN: a greeter you can run\n"
    "STEP: create greet.py with greet(name) returning 'hi ' + name\n"
    "STEP: create shout.py with shout(name) returning greet(name).upper()\n"
)


def chat_reply(user: str) -> str:
    if "plan" in user or "new project" in user:
        return PLAN
    if "go" in user:
        return "Building it.\nBUILD: go"
    if "apply" in user or "looks great" in user:
        return "Applying it.\nAPPLY: it"
    if "throw" in user:
        return "Gone.\nDISCARD: it"
    if "what does" in user:  # a model that applies on its own, unasked
        return "It greets people.\nAPPLY: it"
    return "Sure."


def make(settings: Settings, repo: Path) -> tuple[Chat, Plans, Workspace, FakeBackend]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"]
        if CHAT_PROMPT.splitlines()[0] in msgs[0]["content"]:
            return chat_reply(last.split("]\n\n")[-1].lower())  # past the "[Pablo: ...]" note
        name = re.search(r"create (\w+)\.py", last)
        fn = name.group(1) if name else "x"
        return (f"Done.\n\n{fn}.py\n<<<<<<< SEARCH\n=======\n"
                f"def {fn}(name):\n    return 'hi ' + name\n>>>>>>> REPLACE\n")  # fmt: skip

    fake = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    return Chat(orch, workspace=ws, plans=plans), plans, ws, fake


def test_talk_a_plan_then_build_it_one_approved_step_at_a_time(
    settings: Settings, repo: Path
) -> None:
    chat, plans, _, fake = make(settings, repo)
    conv = chat.start()
    drafted = chat.say(conv.id, "let's plan a greeter")
    assert drafted.run is None and drafted.plan and drafted.plan["state"] == "draft"
    assert "1. create greet.py" in drafted.reply.content and "PLAN:" not in drafted.reply.content
    coder_calls = len(fake.calls)

    built = chat.say(conv.id, "looks right, go")
    plan = plans.get("", conv.id)
    assert plan and plan.state == "running" and "Building step 1" in built.reply.content
    first, second = Plans.steps(plan)
    assert first.status == "needs_you" and first.run and second.status == "queued"
    assert not (repo / "greet.py").exists()  # nothing lands until you say so
    task = next(m for _, m in fake.calls[coder_calls:] if "step 1 of a plan" in m[-1]["content"])
    assert "a greeter you can run" in task[-1]["content"]

    # The model sees what's waiting, and "apply it" applies the step and starts the next one.
    applied = chat.say(conv.id, "apply it")
    assert applied.decided and applied.decided["state"] == "approved"
    assert (repo / "greet.py").is_file()
    plan = plans.get("", conv.id)
    assert plan and Plans.steps(plan)[0].status == "done"
    assert Plans.steps(plan)[1].status == "needs_you"
    shout_task = next(m for _, m in fake.calls if "step 2 of a plan" in m[-1]["content"])
    assert "Already done and in the repo: create greet.py" in shout_task[-1]["content"]

    chat.say(conv.id, "looks great")
    plan = plans.get("", conv.id)
    assert plan and plan.state == "done" and (repo / "shout.py").is_file()


def test_the_model_cant_apply_on_its_own_and_only_the_owner_can(
    settings: Settings, repo: Path
) -> None:
    chat, plans, _, _ = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "plan a greeter")
    chat.say(conv.id, "go")
    # The user didn't say apply; a model that writes APPLY anyway changes nothing.
    turn = chat.say(conv.id, "what does step one do?")
    assert turn.decided is None and not (repo / "greet.py").exists()

    chat.store.ensure_profile("sam", "Sam", "member")
    theirs = chat.start("sam")
    chat.say(theirs.id, "plan a greeter", profile="sam")  # busy: matt's plan is building
    plan = plans.get("", conv.id)
    assert plan and plan.by == "owner"
    refused = chat.say(theirs.id, "apply it", profile="sam")
    assert "Only the owner" in refused.reply.content and not (repo / "greet.py").exists()


def test_a_rejected_step_pauses_the_plan_and_go_tries_it_again(
    settings: Settings, repo: Path
) -> None:
    chat, plans, _, _ = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "plan a greeter")
    chat.say(conv.id, "go")
    with pytest.raises(PlanBusy):
        plans.draft("", "other", ["x"], lane="auto", by="owner", conversation=conv.id)
    chat.say(conv.id, "throw it away")
    plan = plans.get("", conv.id)
    assert plan and plan.state == "paused" and Plans.steps(plan)[0].status == "blocked"
    chat.say(conv.id, "go again")
    plan = plans.get("", conv.id)
    assert plan and plan.state == "running" and Plans.steps(plan)[0].status == "needs_you"


def test_the_api_builds_a_plan_and_approving_a_step_moves_it_along(
    settings: Settings, repo: Path
) -> None:
    _, plans, ws, _ = make(settings, repo)
    app = create_app(ws.base, workspace=ws)
    c = TestClient(app, client=("127.0.0.1", 50000))
    assert c.get("/api/plan").status_code == 404
    plans.draft("", "a greeter", ["create greet.py with greet(name)"], lane="auto", by="owner",
                conversation="")  # fmt: skip
    assert c.get("/api/plan").json()["state"] == "draft"
    started = c.post("/api/plan/go").json()
    step = started["phases"][0]["tasks"][0]
    assert step["status"] == "needs_you" and step["run"]
    assert c.post("/api/plan/go").status_code == 409  # still waiting for you
    assert c.post(f"/api/runs/{step['run']}/approve").status_code == 200
    assert c.get("/api/plan").json()["state"] == "done" and (repo / "greet.py").is_file()


def test_every_step_sees_the_users_own_words(settings: Settings, repo: Path) -> None:
    chat, plans, _, fake = make(settings, repo)
    conv = chat.start()
    chat.say(conv.id, "let's plan a greeter: greet(name) in greet.py returns 'hi ' + name")
    plan = plans.get("", conv.id)
    assert plan and "greet(name) in greet.py" in plan.request
    chat.say(conv.id, "go")
    step_prompt = next(m for _, m in fake.calls if "step 1 of a plan" in m[-1]["content"])
    assert "The user's request (keep its names and behavior):" in step_prompt[-1]["content"]
    assert "returns 'hi ' + name" in step_prompt[-1]["content"]


def test_go_builds_the_reviewed_plan_even_if_the_model_restates_it(
    settings: Settings, repo: Path
) -> None:
    """Saying go to a draft keeps that plan: a restated, shorter plan doesn't replace it."""

    def coder(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"].split("]\n\n")[-1].lower()
        if CHAT_PROMPT.splitlines()[0] in msgs[0]["content"]:
            if "go" in last:
                return "Great, here it is.\nPLAN: greeter\nSTEP: create greet.py with everything"
            return PLAN
        return ("Done.\n\ngreet.py\n<<<<<<< SEARCH\n=======\ndef greet(name):\n"
                "    return 'hi ' + name\n>>>>>>> REPLACE\n")  # fmt: skip

    fake = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    chat = Chat(orch, workspace=ws, plans=plans)
    conv = chat.start()
    chat.say(conv.id, "let's plan a greeter")
    turn = chat.say(conv.id, "looks good, go")
    plan = plans.get("", conv.id)
    assert plan and len(Plans.steps(plan)) == 2 and plan.state == "running"
    assert "Building step 1" in turn.reply.content


def test_a_question_starts_nothing_even_if_the_model_offers_work(
    settings: Settings, repo: Path
) -> None:
    from pablo.convos import is_question

    assert is_question("Do we have tests for by_payee?")
    assert is_question("Explain the difference between total and monthly.")
    assert not is_question("Can you add a test for by_payee?")
    assert not is_question("Make load skip blank lines.")

    fake = FakeBackend(scripts={"pablo/coder": lambda m, x: "No tests yet.\nRUN: add a test"})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    chat = Chat(orch)
    conv = chat.start()
    turn = chat.say(conv.id, "Do we have tests for by_payee?")
    assert turn.run is None and "Nothing started" in turn.reply.content
    assert "RUN:" not in turn.reply.content


def test_the_advisor_plans_and_builds_alone_in_a_standard_project(
    settings: Settings, repo: Path
) -> None:
    """The Advisor tab: its plan's steps are written and judged by the advisor, nothing else."""
    marker = "RUN: <a complete task"  # in both chat prompts, never in a coder or judge prompt

    def advisor(model: str, msgs: Sequence[Message]) -> str:
        last = msgs[-1]["content"]
        if marker in msgs[0]["content"]:
            assert msgs[0]["content"].startswith("You are the advisor")
            return chat_reply(last.split("]\n\n")[-1].lower())
        if "certainty" in msgs[0]["content"].lower():
            return JUDGE
        name = re.search(r"create (\w+)\.py", last)
        fn = name.group(1) if name else "x"
        return (f"Done.\n\n{fn}.py\n<<<<<<< SEARCH\n=======\n"
                f"def {fn}(name):\n    return 'hi ' + name\n>>>>>>> REPLACE\n")  # fmt: skip

    model = settings.lanes["advisor"].model
    fake = FakeBackend(scripts={model: advisor})
    orch = Orchestrator(settings, fake, repo=repo, checks=CHECKS)
    orch.retry_inline = True
    ws = Workspace(orch)
    plans = Plans(ws, background=False)
    chat = Chat(orch, workspace=ws, plans=plans)

    conv = chat.start(lane="advisor")
    drafted = chat.say(conv.id, "let's plan a greeter")
    assert drafted.plan and drafted.plan["lane"] == "advisor"
    assert drafted.reply.lane == "advisor"  # filed under the Advisor, not Hybrid
    chat.say(conv.id, "go")
    plan = plans.get(conversation=conv.id)  # one plan per chat since C14
    assert plan and Plans.steps(plan)[0].status == "needs_you"
    run_id = Plans.steps(plan)[0].run or ""
    run = ws.for_run(run_id).get(run_id)
    assert run.self_judged and run.model == model and run.retry == "off"
    assert {m for m, _ in fake.calls} == {model}  # chat, code and judge: one model
    with pytest.raises(KeyError):
        ws.for_run(run_id).escalate(run_id)  # no Claude on the Advisor's side

    chat.say(conv.id, "apply it")
    assert (repo / "greet.py").is_file()
    assert ws.orch().self_judged is False  # Hybrid in the same project is untouched
