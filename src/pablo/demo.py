"""A scripted team so the cockpit runs on any laptop, no GPU or gateway needed."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import yaml

from pablo.gateway import FakeBackend, Message
from pablo.verify import DEFAULT_CHECKS

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
    return "Added a demo note so you can try approve and reject.\n\n```diff\n" + DEMO_DIFF + "```"


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
        }
    )


def load_checks(repo: Path) -> dict[str, list[str]]:
    """Project checks from .pablo/checks.yaml; Python defaults if it looks like a Python repo."""
    path = repo / ".pablo" / "checks.yaml"
    if path.is_file():
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        return {str(k): (v.split() if isinstance(v, str) else list(v)) for k, v in data.items()}
    if (repo / "pyproject.toml").is_file():
        return dict(DEFAULT_CHECKS)
    return {}
