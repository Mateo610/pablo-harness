"""Per-lane behavior from lanes.yaml: who writes code, and how long they may think."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pablo.config import Settings
from pablo.gateway import Completion, FakeBackend, LiteLLMBackend, Message
from pablo.orchestrator import Orchestrator


class Recording(FakeBackend):
    def __init__(self) -> None:
        super().__init__(scripts={"pablo/judge": ['{"certainty": 0.9, "summary": "ok"}']})
        self.kwargs: list[tuple[str, dict[str, Any]]] = []

    def complete(self, model: str, messages: Sequence[Message], **kw: Any) -> Completion:
        self.kwargs.append((model, kw))
        if model == "pablo/deep":
            return Completion(
                model,
                "Add sub.\n\ncalc.py\n<<<<<<< SEARCH\n    return a + b\n=======\n"
                "    return a + b\n\n\ndef sub(a, b):\n    return a - b\n>>>>>>> REPLACE\n",
            )
        return super().complete(model, messages, **kw)


def test_deep_writes_code_with_its_own_budget(settings: Settings, repo: Path) -> None:
    deep = settings.lane("deep")
    assert deep.writes_code and deep.max_tokens > 4096 and deep.timeout_s >= 3600
    assert not settings.lane("media").writes_code
    backend = Recording()
    run = Orchestrator(settings, backend, repo=repo, checks={}).start("add sub", lane="deep")
    assert "def sub" in run.diff
    model, kw = backend.kwargs[0]
    assert model == "pablo/deep"
    assert kw["max_tokens"] == deep.max_tokens and kw["timeout_s"] == deep.timeout_s
    assert kw["temperature"] == 1.0 and kw["top_p"] == 1.0


def test_coder_keeps_the_default_sampling(settings: Settings, repo: Path) -> None:
    coder = settings.lane("coder")
    assert coder.temperature == 0.2 and coder.top_p is None
    backend = Recording()
    Orchestrator(settings, backend, repo=repo, checks={}).start("say hello", lane="coder")
    model, kw = backend.kwargs[0]
    assert model == "pablo/coder"
    assert kw["temperature"] == 0.2 and kw["top_p"] is None


def test_gateway_sends_top_p_only_when_set(monkeypatch: Any) -> None:
    captured: list[dict[str, Any]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class _Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def post(self, _path: str, *, json: dict[str, Any], timeout: Any) -> _Response:
            del timeout
            captured.append(json)
            return _Response()

    monkeypatch.setattr("pablo.gateway.httpx.Client", _Client)
    backend = LiteLLMBackend("http://127.0.0.1:4000/v1", "test-key")
    messages: list[Message] = [{"role": "user", "content": "hi"}]
    backend.complete("pablo/coder", messages)
    backend.complete("pablo/deep", messages, temperature=1.0, top_p=1.0)
    assert "top_p" not in captured[0]
    assert captured[0]["temperature"] == 0.2
    assert captured[1]["temperature"] == 1.0 and captured[1]["top_p"] == 1.0
