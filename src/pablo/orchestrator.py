"""One run: route → work → verify → judge → ready or "Needs you".

You stay the merge button. The orchestrator never writes to your working tree
without an approve, and never calls the escalation lane without an escalate.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pablo.config import ESCALATION_LANE, Settings
from pablo.events import Event, EventBus
from pablo.gateway import ChatBackend, Message
from pablo.judge import judge
from pablo.router import Router
from pablo.traces import Trace, TraceStore
from pablo.verify import Verdict, apply_diff, verify_diff

RunState = Literal["running", "ready", "needs_you", "approved", "rejected", "failed"]

CODER_PROMPT = """You are the coder on a small local team. Make the smallest correct change.
Reply with a short explanation, then exactly one unified diff in a ```diff fenced block,
with paths relative to the repo root (a/ and b/ prefixes). Include tests when behavior changes."""

ANSWER_PROMPT = """You are a specialist on a small local team. Answer directly and concretely."""

MAX_TREE = 200
MAX_FILE_CHARS = 12_000


@dataclass
class Run:
    id: str
    task: str
    lane: str
    model: str
    state: RunState = "running"
    route_reason: str = ""
    complexity: str = "build"
    explanation: str = ""
    diff: str = ""
    verdict: dict[str, Any] | None = None
    certainty: float | None = None
    judge_summary: str = ""
    question: str = ""
    task_id: str | None = None
    escalated_from: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_diff(text: str) -> tuple[str, str]:
    """Split a model reply into (explanation, diff)."""
    match = re.search(r"```(?:diff|patch)\s*\n(.*?)```", text, re.S)
    if not match:
        return text.strip(), ""
    diff = match.group(1)
    if not diff.endswith("\n"):
        diff += "\n"
    explanation = (text[: match.start()] + text[match.end() :]).strip()
    return explanation, diff


def repo_context(repo: Path, task: str) -> str:
    """Cheap context: the file tree plus any files the task names.

    Milestone 3 replaces this with a repo map + Serena (LSP) symbol lookup.
    """
    try:
        listed = subprocess.run(
            ["git", "ls-files"], cwd=repo, text=True, capture_output=True, check=True
        ).stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        listed = [
            str(p.relative_to(repo))
            for p in repo.rglob("*")
            if p.is_file() and ".git" not in p.parts
        ]
    tree = "\n".join(sorted(listed)[:MAX_TREE])
    named = [f for f in listed if f in task or Path(f).name in task]
    blocks = []
    for rel in named[:6]:
        body = (repo / rel).read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
        blocks.append(f"--- {rel}\n{body}")
    return f"Repo files:\n{tree}\n\n" + "\n\n".join(blocks)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        backend: ChatBackend,
        *,
        repo: Path,
        bus: EventBus | None = None,
        checks: dict[str, list[str]] | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.repo = repo.resolve()
        self.bus = bus or EventBus()
        self.checks = checks
        self.router = Router(settings, backend)
        self.traces = TraceStore(settings.traces_dir)
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ---- helpers -------------------------------------------------------------
    def _emit(self, kind: str, run: Run, **data: Any) -> None:
        self.bus.publish(Event(kind, run.id, {"lane": run.lane, **data}))

    def _save(self, run: Run) -> None:
        with self._lock:
            self.runs[run.id] = run
        folder = self.settings.home / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{run.id}.json").write_text(json.dumps(run.to_dict(), indent=2), "utf-8")

    def get(self, run_id: str) -> Run:
        with self._lock:
            if run_id in self.runs:
                return self.runs[run_id]
        path = self.settings.home / "runs" / f"{run_id}.json"
        if not path.is_file():
            raise KeyError(run_id)
        run = Run(**json.loads(path.read_text("utf-8")))
        with self._lock:
            self.runs[run_id] = run
        return run

    def recent(self, limit: int = 50) -> list[Run]:
        """Newest first. Reads saved runs too, so the inbox survives a restart."""
        folder = self.settings.home / "runs"
        if folder.is_dir():
            files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for path in files[: max(limit, 200)]:
                with self._lock:
                    known = path.stem in self.runs
                if not known:
                    try:
                        self.get(path.stem)
                    except (KeyError, ValueError, TypeError):
                        continue
        with self._lock:
            runs = sorted(self.runs.values(), key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    def inbox(self) -> list[Run]:
        return [r for r in self.recent(200) if r.state in ("needs_you", "ready")]

    # ---- the run -------------------------------------------------------------
    def start(
        self,
        task: str,
        *,
        lane: str | None = None,
        task_id: str | None = None,
        escalate: bool = False,
        escalated_from: str | None = None,
    ) -> Run:
        decision = self.router.route(task, lane=ESCALATION_LANE if escalate else lane)
        lane_cfg = self.settings.lane(decision.lane)
        if lane_cfg.requires_approval and not escalate:
            raise PermissionError(f"lane '{lane_cfg.name}' only runs when you escalate")

        run = Run(
            id=uuid.uuid4().hex[:10],
            task=task,
            lane=lane_cfg.name,
            model=lane_cfg.model,
            route_reason=decision.reason,
            complexity=decision.complexity,
            task_id=task_id,
            escalated_from=escalated_from,
        )
        self._save(run)
        self._emit("route", run, reason=decision.reason, confidence=decision.confidence)
        try:
            self._work(run)
        except Exception as exc:
            run.state = "failed"
            run.question = f"Run failed: {exc}"
            self._save(run)
            self._emit("error", run, message=str(exc))
            raise
        return run

    def _work(self, run: Run) -> None:
        wants_diff = run.lane in ("coder", ESCALATION_LANE)
        messages: list[Message] = [
            {"role": "system", "content": CODER_PROMPT if wants_diff else ANSWER_PROMPT},
            {"role": "user", "content": f"{run.task}\n\n{repo_context(self.repo, run.task)}"},
        ]
        self._emit("status", run, text=f"{run.lane} · thinking")
        reply = self.backend.complete(run.model, messages)
        run.explanation, run.diff = extract_diff(reply.text)

        verdict: Verdict | None = None
        if run.diff:
            self._emit("status", run, text=f"{run.lane} · running checks")
            verdict = verify_diff(self.repo, run.diff, self.checks)
            run.verdict = verdict.to_dict()
            self._emit("evidence", run, summary=verdict.summary(), passed=verdict.passed)

            self._emit("status", run, text="judge · reviewing")
            judgement = judge(
                self.backend, self.settings.lane("judge").model, run.task, run.diff, verdict
            )
            run.certainty = judgement.certainty
            run.judge_summary = judgement.summary
            run.question = judgement.question
            ready = (
                verdict.passed and judgement.certainty >= self.settings.thresholds.judge_needs_you
            )
            run.state = "ready" if ready else "needs_you"
            self._emit("judged", run, certainty=judgement.certainty, state=run.state)
        else:
            run.state = "ready"

        lane_cfg = self.settings.lane(run.lane)
        self.traces.append(
            Trace(
                run_id=run.id,
                lane=run.lane,
                model=run.model,
                messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                output=reply.text,
                lane_train_ok=lane_cfg.train_ok,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                latency_s=reply.latency_s,
                verdict=run.verdict,
                certainty=run.certainty,
            )
        )
        self._save(run)
        if run.state == "needs_you":
            self._emit("needs_you", run, question=run.question)

    # ---- your decisions -----------------------------------------------------
    def approve(self, run_id: str) -> Run:
        run = self.get(run_id)
        if run.state not in ("ready", "needs_you"):
            raise ValueError(f"run {run_id} is {run.state}")
        if run.diff:
            ok, err = apply_diff(self.repo, run.diff)
            if not ok:
                raise RuntimeError(f"diff no longer applies: {err}")
        run.state = "approved"
        self.traces.record_decision(run.id, "approved")
        self._save(run)
        self._emit("decided", run, decision="approved")
        return run

    def reject(self, run_id: str) -> Run:
        run = self.get(run_id)
        run.state = "rejected"
        self.traces.record_decision(run.id, "rejected")
        self._save(run)
        self._emit("decided", run, decision="rejected")
        return run

    def escalate(self, run_id: str) -> Run:
        """Send the same task to Claude. Its trace is never trainable."""
        original = self.get(run_id)
        if ESCALATION_LANE not in self.settings.lanes:
            raise KeyError("no escalation lane configured")
        return self.start(
            original.task, escalate=True, escalated_from=original.id, task_id=original.task_id
        )
