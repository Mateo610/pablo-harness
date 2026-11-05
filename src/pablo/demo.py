"""A scripted team so the cockpit runs on any laptop, no GPU or gateway needed."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import yaml

from pablo.gateway import FakeBackend, Message
from pablo.verify import DEFAULT_CHECKS, IMPORTS_CHECK

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


def load_checks(repo: Path) -> dict[str, list[str]]:
    """Project checks from .pablo/checks.yaml; Python defaults if it looks like a Python repo.

    Any Python repo also gets the `imports` check unless checks.yaml sets `imports: off`.
    """
    path = repo / ".pablo" / "checks.yaml"
    checks: dict[str, list[str]] = {}
    imports_off = False
    if path.is_file():
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        for key, value in data.items():
            if str(key) == "imports" and value in (False, "off", None):
                imports_off = True
                continue
            checks[str(key)] = value.split() if isinstance(value, str) else list(value)
    elif (repo / "pyproject.toml").is_file():
        checks = dict(DEFAULT_CHECKS)
    if not imports_off and "imports" not in checks and _has_python(repo):
        checks["imports"] = list(IMPORTS_CHECK)
    return checks


def _has_python(repo: Path) -> bool:
    for base in (repo, repo / "src"):
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if path.suffix == ".py" or (path / "__init__.py").is_file():
                return True
    return False
