"""Per-lane behavior from lanes.yaml: who writes code, and how long they may think."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pablo.config import Settings
from pablo.gateway import Completion, FakeBackend, Message
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
