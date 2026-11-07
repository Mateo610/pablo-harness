"""Chat replies stream, chat runs work in the background, runs link back to their chat."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from tests.fakes import FakeBackend

from pablo.api import create_app
from pablo.config import Settings
from pablo.convos import CHAT_PROMPT
from pablo.gateway import LiteLLMBackend, Message
from pablo.orchestrator import Orchestrator

CHECKS = {"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}
JUDGE = json.dumps({"certainty": 0.9, "summary": "ok", "question": ""})
GOOD = (
    "Added sub.\n\ncalc.py\n<<<<<<< SEARCH\ndef add(a, b):\n    return a + b\n=======\n"
    "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n>>>>>>> REPLACE\n"
)
REPLY = (
    "On it, adding it now.\nRUN: add sub(a, b) to calc.py next to add\nREMEMBER: likes small diffs"
)


def client(settings: Settings, repo: Path) -> tuple[TestClient, Orchestrator]:
    def coder(model: str, msgs: Sequence[Message]) -> str:
        return REPLY if CHAT_PROMPT in msgs[0]["content"] else GOOD

    backend = FakeBackend(scripts={"pablo/coder": coder, "pablo/judge": lambda m, x: JUDGE})
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    return TestClient(create_app(orch), client=("127.0.0.1", 5000)), orch


def ndjson(resp: httpx.Response) -> list[dict]:  # type: ignore[type-arg]
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def wait_for(fn, timeout: float = 20.0):  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if value := fn():
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_a_reply_streams_then_the_turn_arrives(settings: Settings, repo: Path) -> None:
    c, _ = client(settings, repo)
    conv = c.post("/api/conversations", json={"lane": "coder"}).json()
    events = ndjson(c.post(f"/api/conversations/{conv['id']}/messages",
                           json={"text": "add a sub function", "stream": True}))  # fmt: skip
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert len(deltas) > 3 and "".join(deltas) == REPLY
    done = events[-1]
    assert done["type"] == "done" and "RUN:" not in done["turn"]["reply"]["content"]
    assert done["turn"]["proposed"][0]["text"] == "likes small diffs"


def test_a_chat_run_works_in_the_background_and_links_back(settings: Settings, repo: Path) -> None:
    c, _ = client(settings, repo)
    conv = c.post("/api/conversations", json={"lane": "coder"}).json()
    turn = c.post(f"/api/conversations/{conv['id']}/messages", json={"text": "add sub"}).json()
    rid = turn["run"]["id"]
    run = wait_for(lambda: (r := c.get(f"/api/runs/{rid}").json())["state"] != "running" and r)
    assert run["state"] == "ready" and run["conversation_id"] == conv["id"]
    assert run["conversation_lane"] == "coder" and run["by"] == "owner"
    assert any(r["conversation_id"] == conv["id"] for r in c.get("/api/inbox").json())


def test_a_stream_ends_with_an_error_it_can_show(settings: Settings, repo: Path) -> None:
    c, _ = client(settings, repo)
    conv = c.post("/api/conversations", json={"lane": "claude"}).json()
    events = ndjson(c.post(f"/api/conversations/{conv['id']}/messages",
                           json={"text": "hi", "stream": True}))  # fmt: skip
    assert events == [{"type": "error", "status": 403, "detail": events[0]["detail"]}]
    assert "confirm" in events[0]["detail"]


def test_start_background_returns_before_the_work(settings: Settings, repo: Path) -> None:
    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: (time.sleep(0.3), GOOD)[1],
                                   "pablo/judge": lambda m, x: JUDGE})  # fmt: skip
    orch = Orchestrator(settings, backend, repo=repo, checks=CHECKS)
    run = orch.start_background("add sub(a, b)", lane="coder", by="owner")
    assert run.state == "running"
    assert wait_for(lambda: orch.get(run.id).state == "ready")


def test_the_gateway_streams_content_and_never_reasoning() -> None:
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "secret plan"}}]},
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 2}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    gw = LiteLLMBackend("http://gw/v1", "k")
    gw._client = httpx.Client(base_url="http://gw/v1", transport=httpx.MockTransport(handler))
    pieces: list[str] = []
    done = gw.stream("pablo/coder", [{"role": "user", "content": "hi"}], on_delta=pieces.append)
    assert pieces == ["Hel", "lo"] and done.text == "Hello"
    assert (done.prompt_tokens, done.completion_tokens) == (11, 2)
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}
