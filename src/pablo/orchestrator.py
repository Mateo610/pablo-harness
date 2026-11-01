"""One run: route → work → verify → judge → ready or "Needs you".

You stay the merge button. The orchestrator never writes to your working tree
without an approve, and never calls the escalation lane without an escalate.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pablo.config import ESCALATION_LANE, Settings
from pablo.context import build_coder_context
from pablo.edits import EditError, blocks_to_diff, has_blocks, parse_blocks, strip_blocks
from pablo.events import Event, EventBus
from pablo.gateway import ChatBackend, Message
from pablo.hunks import all_ids
from pablo.hunks import select as select_hunks
from pablo.judge import judge
from pablo.router import Router
from pablo.sandbox import Sandbox, make_sandbox
from pablo.swap import SwapView, model_id
from pablo.traces import Trace, TraceStore
from pablo.verify import Verdict, _recount_hunks, apply_diff, verify_diff

RunState = Literal["running", "ready", "needs_you", "approved", "rejected", "failed"]

CODER_PROMPT = """You are the coder on a small local team. Make the smallest correct change.
Reply with a short explanation, then exactly one unified diff in a ```diff fenced block,
with paths relative to the repo root (a/ and b/ prefixes). Include tests when behavior changes.
The diff must apply with `git apply`:
- No `index` lines, and no code after the @@ header.
- A context line starts with one space. A blank context line is one space, never an empty line.
- The counts in @@ -start,count +start,count @@ must match the lines in that hunk.
- Copy context exactly from the numbered files. The number before | is the line
  number; do not copy the number or the |.
- A new test must import every name it calls.
End the diff with a newline."""

REPAIR_PROMPT = """That diff did not apply. `git apply` said:

{error}

Re-read the numbered files and reply with one corrected unified diff in a ```diff block.
No `index` lines. A blank context line is a single space.
Hunk counts must match the lines that follow.
Copy context exactly, without the line number or the |. End the diff with a newline."""

CODER_BLOCKS_PROMPT = """You are the coder on a small local team. Make the smallest correct change.
Include tests when behavior changes. A new test must import every name it calls.
Reply with a short explanation, then one edit block per change, in exactly this format:

path/to/file.py
<<<<<<< SEARCH
lines copied exactly from the current file
=======
the new lines
>>>>>>> REPLACE

Rules:
- SEARCH must match the file exactly and in only one place. Copy whole lines, without the
  line number and | that prefix the files above.
- Keep blocks small: the lines you change plus one or two around them, not the whole file.
- Use several blocks for several places. To create a new file, leave SEARCH empty."""

REPAIR_BLOCKS_PROMPT = """Those edits could not be applied: {error}

Re-read the numbered files and reply with the complete, corrected set of edit blocks.
Copy SEARCH lines exactly as they are in the file, without line numbers or |."""

ANSWER_PROMPT = """You are a specialist on a small local team. Answer directly and concretely."""


class PartialChecksFailed(RuntimeError):
    def __init__(self, verdict: Verdict) -> None:
        super().__init__(f"checks failed on the hunks you kept: {verdict.summary()}")
        self.verdict = verdict


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
    group: str | None = None  # runs started together by compare()
    applied_hunks: list[str] | None = None  # set when you approve only part of the diff
    repaired: bool = False  # the coder fixed its own diff after git apply failed
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_diff(text: str) -> tuple[str, str]:
    """Split a model reply into (explanation, diff)."""
    match = re.search(r"```(?:diff|patch)\s*\n(.*?)```", text, re.S)
    if not match:
        return text.strip(), ""
    diff = _clean_diff(match.group(1))
    explanation = (text[: match.start()] + text[match.end() :]).strip()
    return explanation, diff


def _clean_diff(diff: str) -> str:
    """Make a model diff something `git apply` can read.

    Models invent non-hex `index` hashes, and they drop the leading space on a
    blank context line. Both make git report a corrupt patch.
    """
    lines: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("index "):
            continue
        if line.startswith("@@"):
            in_hunk = True
            lines.append(line)
            continue
        if line.startswith(("diff --git", "--- ")):
            in_hunk = False
            lines.append(line)
            continue
        if in_hunk and line == "":
            lines.append(" ")
            continue
        lines.append(line)
    text = "\n".join(_recount_hunks(lines))
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def repo_context(repo: Path, task: str) -> str:
    """The coder's context: task, one skill card, file tree, numbered files, under a budget."""
    return build_coder_context(repo, task)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        backend: ChatBackend,
        *,
        repo: Path,
        bus: EventBus | None = None,
        checks: dict[str, list[str]] | None = None,
        swap: SwapView | None = None,
        sandbox: Sandbox | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.repo = repo.resolve()
        self.bus = bus or EventBus()
        self.checks = checks
        self.swap = swap
        # Where checks run. lanes.yaml `sandbox:` decides unless the caller picks one.
        self.sandbox = sandbox or make_sandbox(settings.sandbox, self.repo)
        self.router = Router(settings, backend)
        self.traces = TraceStore(settings.traces_dir)
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ---- helpers -------------------------------------------------------------
    def _emit(self, kind: str, run: Run, **data: Any) -> None:
        event = Event(kind, run.id, {"lane": run.lane, **data})
        self.bus.publish(event)
        folder = self.settings.home / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{run.id}.events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")

    def _swapping(self, model: str) -> bool:
        if self.swap is None:
            return False
        return self.swap.state(model_id(model)) not in ("ready", "unknown")

    def _during_swap(
        self,
        run: Run,
        lane_name: str,
        model: str,
        settled: str,
        call: Callable[[], Any],
        *,
        announce: bool = True,
    ) -> Any:
        """Run `call`. Emit '{lane} · loading' first when the swapper must load `model`."""
        if not self._swapping(model):
            if announce:
                self._emit("status", run, text=settled)
            return call()

        self._emit("status", run, text=f"{lane_name} · loading")
        box: dict[str, Any] = {}

        def run_call() -> None:
            try:
                box["value"] = call()
            except Exception as exc:
                box["error"] = exc

        thread = threading.Thread(target=run_call, daemon=True)
        thread.start()
        announced = False
        while thread.is_alive():
            if (
                not announced
                and self.swap is not None
                and self.swap.state(model_id(model)) == "ready"
            ):
                self._emit("status", run, text=settled)
                announced = True
            thread.join(0.2)
        if not announced:
            self._emit("status", run, text=settled)
        error = box.get("error")
        if isinstance(error, BaseException):
            raise error
        return box["value"]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        """Everything that happened in a run, in order: what replay plays back."""
        path = self.settings.home / "runs" / f"{run_id}.events.jsonl"
        if not path.is_file():
            self.get(run_id)  # raises KeyError for unknown runs
            return []
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]

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
        group: str | None = None,
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
            group=group,
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

    def _extract(self, text: str) -> tuple[str, str, str]:
        """(explanation, diff, edit error) from a coder reply in either edit format."""
        if has_blocks(text):
            explanation = strip_blocks(text)
            try:
                return explanation, blocks_to_diff(self.repo, parse_blocks(text)), ""
            except EditError as exc:
                return explanation, "", str(exc)
        explanation, diff = extract_diff(text)
        return explanation, diff, ""

    def _work(self, run: Run) -> None:
        lane = self.settings.lane(run.lane)
        wants_diff = lane.writes_code or run.lane in ("coder", ESCALATION_LANE)
        blocks = lane.edit_format == "blocks"
        system = (CODER_BLOCKS_PROMPT if blocks else CODER_PROMPT) if wants_diff else ANSWER_PROMPT
        messages: list[Message] = [
            {"role": "system", "content": system},
            {"role": "user", "content": repo_context(self.repo, run.task)},
        ]
        reply = self._during_swap(
            run,
            run.lane,
            run.model,
            f"{run.lane} · thinking",
            lambda: self.backend.complete(
                run.model, messages, max_tokens=lane.max_tokens, timeout_s=lane.timeout_s
            ),
        )
        run.explanation, run.diff, edit_error = self._extract(reply.text)
        verdict: Verdict | None = None
        if run.diff:
            self._emit("status", run, text=f"{run.lane} · running checks")
            verdict = verify_diff(self.repo, run.diff, self.checks, sandbox=self.sandbox)

        # Local models slip on exact text. Give the coder one chance to fix its own edits,
        # with the precise error, before anything is judged.
        problem = edit_error or (verdict.error if verdict and not verdict.applied else "")
        if wants_diff and problem:
            self._emit("status", run, text=f"{run.lane} · edits didn't apply, repairing")
            repair = REPAIR_BLOCKS_PROMPT if blocks else REPAIR_PROMPT
            messages = [
                *messages,
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": repair.format(error=problem)},
            ]
            reply = self._during_swap(
                run,
                run.lane,
                run.model,
                f"{run.lane} · thinking",
                lambda: self.backend.complete(
                    run.model, messages, max_tokens=lane.max_tokens, timeout_s=lane.timeout_s
                ),
                announce=False,
            )
            explanation, repaired, edit_error = self._extract(reply.text)
            if repaired:
                run.explanation = explanation or run.explanation
                run.diff = repaired
                run.repaired = True
                verdict = verify_diff(self.repo, run.diff, self.checks, sandbox=self.sandbox)

        if run.diff and verdict is not None:
            checked = verdict
            run.verdict = checked.to_dict()
            self._emit("evidence", run, summary=checked.summary(), passed=checked.passed)

            judge_model = self.settings.lane("judge").model
            judgement = self._during_swap(
                run,
                "judge",
                judge_model,
                "judge · reviewing",
                lambda: judge(self.backend, judge_model, run.task, run.diff, checked),
            )
            run.certainty = judgement.certainty
            run.judge_summary = judgement.summary
            run.question = judgement.question
            ready = (
                verdict.passed and judgement.certainty >= self.settings.thresholds.judge_needs_you
            )
            run.state = "ready" if ready else "needs_you"
            self._emit("judged", run, certainty=judgement.certainty, state=run.state)
        elif wants_diff and edit_error:
            run.state = "needs_you"
            run.question = (
                f"The coder's edits could not be applied ({edit_error}). Retry or escalate?"
            )
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
        self._emit("finished", run, state=run.state)

    # ---- your decisions -----------------------------------------------------
    def approve(self, run_id: str, hunks: list[str] | None = None, *, force: bool = False) -> Run:
        """Apply the diff, or only the hunks you kept.

        A partial diff is re-checked first, because dropping a hunk can break the rest.
        If those checks fail you get PartialChecksFailed; `force` applies it anyway.
        Partial approvals are not training data: the model's output wasn't what you kept.
        """
        run = self.get(run_id)
        if run.state not in ("ready", "needs_you"):
            raise ValueError(f"run {run_id} is {run.state}")
        patch = run.diff
        decision = "approved"
        if run.diff and hunks is not None:
            every = all_ids(run.diff)
            keep = set(hunks)
            if not keep:
                raise ValueError("no hunks selected; reject the run instead")
            if keep != set(every):
                patch = select_hunks(run.diff, keep)
                decision = "approved_partial"
                verdict = verify_diff(self.repo, patch, self.checks, sandbox=self.sandbox)
                if not verdict.passed and not force:
                    raise PartialChecksFailed(verdict)
                run.applied_hunks = [h for h in every if h in keep]
        if patch:
            ok, err = apply_diff(self.repo, patch)
            if not ok:
                raise RuntimeError(f"diff no longer applies: {err}")
        run.state = "approved"
        self.traces.record_decision(run.id, decision)
        self._save(run)
        self._emit("decided", run, decision=decision)
        if run.group:
            for other in self.recent(200):
                if (
                    other.group == run.group
                    and other.id != run.id
                    and other.state
                    in (
                        "ready",
                        "needs_you",
                    )
                ):
                    self.reject(other.id)
        return run

    def reject(self, run_id: str) -> Run:
        run = self.get(run_id)
        run.state = "rejected"
        self.traces.record_decision(run.id, "rejected")
        self._save(run)
        self._emit("decided", run, decision="rejected")
        return run

    def compare(self, task: str, lanes: list[str], *, task_id: str | None = None) -> list[Run]:
        """Run the same task on several lanes. Approving one rejects the others."""
        if len(set(lanes)) < 2:
            raise ValueError("compare needs at least two different lanes")
        for name in lanes:
            if self.settings.lane(name).requires_approval:
                raise PermissionError(f"lane '{name}' only runs when you escalate")
        group = uuid.uuid4().hex[:8]
        runs = []
        for name in lanes:
            run = self.start(task, lane=name, task_id=task_id, group=group)
            runs.append(run)
        return runs

    def escalate(self, run_id: str) -> Run:
        """Send the same task to Claude. Its trace is never trainable."""
        original = self.get(run_id)
        if ESCALATION_LANE not in self.settings.lanes:
            raise KeyError("no escalation lane configured")
        return self.start(
            original.task, escalate=True, escalated_from=original.id, task_id=original.task_id
        )
