"""`pablo eval-chat`: does the chat model talk, start one change, or plan, when it should?

Every message in `evals/chat/messages.yaml` goes into a fresh conversation about a seed repo
(in a throwaway home). Pablo records what the reply chose: a plan (PLAN/STEP lines), a run
(a RUN line), or just talk. Runs are recorded, not carried out; no GPU time goes to coding.
"""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from pablo.config import Settings
from pablo.gateway import ChatBackend

CHAT_SET = Path(__file__).resolve().parents[2] / "evals" / "chat"
CHAT_RESULTS = CHAT_SET.parent / "chat-results.jsonl"
LABELS = ("talk", "run", "plan")


@dataclass
class ChatCase:
    label: str
    text: str
    chose: str = ""
    reply: str = ""
    wall_s: float = 0.0


def load_chat_set(root: Path = CHAT_SET) -> tuple[str, list[ChatCase]]:
    data = yaml.safe_load((root / "messages.yaml").read_text("utf-8"))
    return str(data["repo"]), [ChatCase(m["label"], m["text"]) for m in data["messages"]]


def run_chat_eval(
    settings: Settings,
    backend: ChatBackend,
    *,
    lane: str = "auto",
    spoken: bool = False,
    root: Path = CHAT_SET,
    on_case: Any = None,
) -> list[ChatCase]:
    from pablo.convos import Chat
    from pablo.evals import load_suite, materialize
    from pablo.orchestrator import Orchestrator, Run
    from pablo.plans import Plans
    from pablo.projects import Workspace

    class Recording(Chat):
        """A chat whose RUN lines are written down instead of run."""

        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            self.started: list[str] = []

        def _run(self, task: str, lane: str, notes: list[str], profile: str, *_: Any,
                 **__: Any) -> Run:  # fmt: skip
            self.started.append(task)
            return Run(id=uuid.uuid4().hex[:10], task=task, lane=lane, model="", state="ready")

    repo_name, cases = load_chat_set(root)
    seed = next(t for t in load_suite() if t.repo == repo_name)
    with tempfile.TemporaryDirectory(prefix="pablo-chat-eval-") as tmp:
        work = Path(tmp)
        isolated = settings.model_copy(update={"home": work / "home"}, deep=True)
        isolated.gpu.shared_dir = settings.gpu.shared_dir or str(settings.home)
        isolated.gpu.idle_s = 0
        isolated.projects.root = str(work / "projects")
        repo = materialize(seed, work)
        orch = Orchestrator(isolated, backend, repo=repo, checks={})
        ws = Workspace(orch)
        plans = Plans(ws, background=False)
        chat = Recording(orch, workspace=ws, plans=plans)
        for case in cases:
            chat.started = []
            plan_file = repo / ".pablo" / "plan.yaml"
            plan_file.unlink(missing_ok=True)
            conv = chat.start(lane=lane)
            started = time.perf_counter()
            turn = chat.say(conv.id, case.text, spoken=spoken)
            case.wall_s = round(time.perf_counter() - started, 1)
            case.chose = "plan" if turn.plan else "run" if chat.started else "talk"
            case.reply = turn.reply.content[:400]
            if on_case:
                on_case(case)
    return cases


def chat_summary(cases: list[ChatCase], *, lane: str, spoken: bool, commit: str) -> dict[str, Any]:
    confusion = {want: {got: 0 for got in LABELS} for want in LABELS}
    for c in cases:
        confusion[c.label][c.chose] += 1
    right = sum(c.label == c.chose for c in cases)
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "commit": commit,
        "lane": lane,
        "spoken": spoken,
        "cases": len(cases),
        "right": right,
        "accuracy": round(right / len(cases), 3) if cases else 0.0,
        "by_label": {
            want: round(confusion[want][want] / max(1, sum(confusion[want].values())), 3)
            for want in LABELS
        },
        "confusion": confusion,  # wanted -> chosen -> count
        "results": [asdict(c) for c in cases],
    }


def append_chat(summary: dict[str, Any], path: Path = CHAT_RESULTS) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")


def chat_markdown(summary: dict[str, Any]) -> str:
    c = summary["confusion"]
    rows = "\n".join(
        f"| {want} | " + " | ".join(str(c[want][got]) for got in LABELS) + " |" for want in LABELS
    )
    voice = " · spoken" if summary["spoken"] else ""
    return (
        f"### Chat behavior · {summary['lane']}{voice} · `{summary['commit']}`\n\n"
        f"Right {summary['right']}/{summary['cases']} ({summary['accuracy']:.0%}) · by label "
        f"{summary['by_label']}\n\n| wanted \\ chose | talk | run | plan |\n|---|---|---|---|\n"
        + rows
    )
