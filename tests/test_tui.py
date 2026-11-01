from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

from textual.widgets import ListView

from pablo.tui import PabloTUI, render_plan, render_run

Json = dict[str, Any]


def make_run(run_id: str, state: str = "needs_you", certainty: float = 0.41) -> Json:
    return {
        "id": run_id,
        "task": f"task {run_id}",
        "lane": "coder",
        "model": "pablo/coder",
        "state": state,
        "explanation": "did a thing",
        "diff": "diff --git a/x b/x\n+new\n",
        "verdict": {
            "applied": True,
            "passed": True,
            "checks": [{"name": "tests", "passed": True, "exit_code": 0, "duration_s": 0.4}],
            "added": 1,
            "removed": 0,
            "files": ["x"],
            "error": "",
        },
        "certainty": certainty,
        "question": "Keep it?",
    }


PLAN: Json = {
    "project": "demo",
    "goal": "",
    "phases": [
        {
            "id": 1,
            "title": "Core",
            "gate": "it works",
            "tasks": [
                {"id": "1.1", "title": "first", "status": "done", "lane": None},
                {"id": "1.2", "title": "second", "status": "queued", "lane": None},
            ],
        }
    ],
}


class FakeAPI:
    def __init__(self) -> None:
        self.runs = [make_run("r1"), make_run("r2", "ready", 0.9)]
        self.decisions: list[tuple[str, str]] = []
        self.started: list[tuple[str, str | None]] = []

    def health(self) -> Json:
        return {"ok": True}

    def plan(self) -> Json | None:
        return PLAN

    def inbox(self) -> list[Json]:
        return [r for r in self.runs if r["state"] in ("needs_you", "ready")]

    def runs(self, limit: int = 30) -> list[Json]:
        return self.runs[:limit]

    def start(self, task: str, lane: str | None = None, task_id: str | None = None) -> Json:
        self.started.append((task, task_id))
        run = make_run(f"n{len(self.started)}")
        self.runs.append(run)
        return run

    def decide(self, run_id: str, decision: str) -> Json:
        self.decisions.append((run_id, decision))
        for run in self.runs:
            if run["id"] == run_id and decision in ("approve", "reject"):
                run["state"] = "approved" if decision == "approve" else "rejected"
        return {"id": run_id}

    def events(self) -> Iterator[Json]:
        return iter(())


async def settle(pilot: Any) -> None:
    for _ in range(5):
        await pilot.pause(0.05)


def test_render_helpers() -> None:
    assert render_run(make_run("r1")) is not None
    assert render_plan(PLAN) is not None
    assert render_plan(None) is not None


def test_inbox_approve_and_reject() -> None:
    async def scenario() -> None:
        api = FakeAPI()
        app = PabloTUI(api, listen=False)
        async with app.run_test() as pilot:
            await settle(pilot)
            assert len(app.query_one("#inbox", ListView)) == 2
            await pilot.press("a")
            await settle(pilot)
            assert api.decisions == [("r1", "approve")]
            assert len(app.query_one("#inbox", ListView)) == 1
            await pilot.press("r")
            await settle(pilot)
            assert api.decisions[-1] == ("r2", "reject")

    asyncio.run(scenario())


def test_escalate_needs_two_presses() -> None:
    async def scenario() -> None:
        api = FakeAPI()
        app = PabloTUI(api, listen=False)
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("e")
            await settle(pilot)
            assert api.decisions == []
            await pilot.press("e")
            await settle(pilot)
            assert api.decisions == [("r1", "escalate")]

    asyncio.run(scenario())


def test_new_task_and_run_next() -> None:
    async def scenario() -> None:
        api = FakeAPI()
        app = PabloTUI(api, listen=False)
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("n")
            await pilot.press(*"fix it")
            await pilot.press("enter")
            await settle(pilot)
            await pilot.press("x")
            await settle(pilot)
            assert api.started == [("fix it", None), ("second", "1.2")]

    asyncio.run(scenario())
