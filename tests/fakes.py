"""Test doubles: a scripted backend, and a scripted team for whole-flow tests. Never shipped."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pablo.gateway import Completion, Delta, Message

DEMO_DIFF = """diff --git a/PABLO_DEMO.md b/PABLO_DEMO.md
new file mode 100644
--- /dev/null
+++ b/PABLO_DEMO.md
@@ -0,0 +1,3 @@
+# Hello from the fake coder
+
+Approve this run and this file lands in your repo. Reject it and nothing changes.
diff --git a/pablo-demo/hello.txt b/pablo-demo/hello.txt
new file mode 100644
--- /dev/null
+++ b/pablo-demo/hello.txt
@@ -0,0 +1,2 @@
+A second hunk, so you can try keeping one and dropping the other.
+Toggle "Keep" off on either hunk, then apply the rest.
"""


def _coder(model: str, messages: Sequence[Message]) -> str:
    if messages and messages[0]["content"].startswith(("You are Pablo,", "You are the advisor")):
        return _chat(messages[-1]["content"])
    return "Added a demo note so you can try approve and reject.\n\n```diff\n" + DEMO_DIFF + "```"


def _chat(said: str) -> str:
    """What a chat model would say, so a demo of the cockpit reads like the real thing."""
    words = said.lower()
    if any(w in words for w in ("add", "fix", "change", "make", "write", "rename", "remove")):
        return (
            "Sure. I'll hand that to the coder and you'll see the diff and the checks here.\n"
            f"RUN: {said.strip()}"
        )
    return (
        "These are **fake models**, so this is a canned answer. On Pablo, the coder reads the "
        "files that matter and answers here.\n\n"
        "- Ask a question and it just talks.\n"
        "- Ask for a change and it starts a run you approve.\n\n"
        "```python\ndef add(a, b):\n    return a + b\n```\n"
        "REMEMBER: tried the demo cockpit"
    )


def _judge(model: str, messages: Sequence[Message]) -> str:
    return json.dumps(
        {
            "certainty": 0.62,
            "summary": "Harmless demo file.",
            "question": "Keep the demo note in the repo?",
        }
    )


def _router(model: str, messages: Sequence[Message]) -> str:
    return json.dumps({"lane": "coder", "confidence": 0.7})


def demo_backend() -> FakeBackend:
    return FakeBackend(
        scripts={
            "pablo/coder": _coder,
            "pablo/escalate": _coder,
            "pablo/judge": _judge,
            "pablo/router": _router,
            "pablo/advisor": lambda m, msgs: (
                "Blunt take, from a fake advisor: ship the smallest version, then measure."
            ),
            "pablo/deep": _coder,
        }
    )


Responder = Callable[[str, Sequence[Message]], str]


@dataclass
class FakeBackend:
    """Scripted backend for tests: no GPU or gateway.

    `scripts` maps a model alias to a list of replies (consumed in order) or a
    function of (model, messages). Unknown models echo the last user message.
    """

    scripts: dict[str, list[str] | Responder] = field(default_factory=dict)
    calls: list[tuple[str, list[Message]]] = field(default_factory=list)

    def complete(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        top_p: float | None = None,
        max_tokens: int = 4096,
        timeout_s: float | None = None,
    ) -> Completion:
        del temperature, top_p
        self.calls.append((model, list(messages)))
        script = self.scripts.get(model)
        if callable(script):
            text = script(model, messages)
        elif script:
            text = script.pop(0)
        else:
            text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return Completion(
            model=model,
            text=text,
            prompt_tokens=sum(len(m["content"]) // 4 for m in messages),
            completion_tokens=len(text) // 4,
        )

    def stream(
        self, model: str, messages: Sequence[Message], *, on_delta: Delta, **kw: Any
    ) -> Completion:
        """The scripted reply, in small pieces, so streaming code paths run in tests."""
        done = self.complete(model, messages, **kw)
        for i in range(0, len(done.text), 12):
            on_delta(done.text[i : i + 12])
        return done
