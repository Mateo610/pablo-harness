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
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pablo.config import ESCALATION_LANE, Lane, Settings
from pablo.context import Budget, build_coder_context, clip, context_files, numbered, tokens
from pablo.edits import (
    EditError,
    blocks_on_top,
    blocks_to_diff,
    has_blocks,
    parse_blocks,
    strip_blocks,
)
from pablo.events import Event, EventBus
from pablo.gateway import ChatBackend, Completion, Message
from pablo.gpu import GpuScheduler, Priority, ScheduledBackend, off_gpu_models
from pablo.gpu import job as gpu_job
from pablo.hunks import all_ids
from pablo.hunks import select as select_hunks
from pablo.judge import Judgement, judge
from pablo.lessons import Example, Failure, Lessons, Outcome, first_error
from pablo.lookup import LookupClient, ProcessLookup, parse_symbol_request
from pablo.router import Router
from pablo.sandbox import Sandbox, make_sandbox
from pablo.spec import SPEC_CAP, SpecResult, run_spec, write_spec
from pablo.swap import SwapView, model_id
from pablo.tasks import detect_task_type
from pablo.traces import Trace, TraceStore
from pablo.verify import (
    DEFAULT_CHECKS,
    Baselines,
    Verdict,
    _recount_hunks,
    apply_diff,
    verify_diff,
)

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

ASK_SYMBOLS_PROMPT = """You are the coder on a small local team. Before editing, name the symbols
you need to look up (functions, classes, methods). Reply with JSON only: a list of names, like
["load_line", "Entry"]. Do not write edit blocks or a diff yet."""

EDIT_WITH_LOOKUP_PROMPT = """Here are definitions and references for the symbols you asked for:

{lookup}

Now make the smallest correct change for the task. Include tests when behavior changes.
A new test must import every name it calls. Reply with a short explanation, then edit blocks:

path/to/file.py
<<<<<<< SEARCH
lines copied exactly from the current file
=======
the new lines
>>>>>>> REPLACE

SEARCH must match the file exactly. Keep blocks small. To create a new file, leave SEARCH empty."""

REPAIR_BLOCKS_PROMPT = """Those edits could not be applied: {error}

Re-read the numbered files and reply with the complete, corrected set of edit blocks.
Copy SEARCH lines exactly as they are in the file, without line numbers or |."""

FIX_BLOCKS_PROMPT = """Your edits applied, but these checks are now worse than before your change:

{failures}

Fix it. If the code is wrong, fix the code. If a test you just wrote expects the wrong thing, fix
that test. Never weaken, skip, or delete a test of the behavior the task asked for, and never patch
the code under test from a test, conftest, or new module.
Reply with edit blocks. SEARCH may copy the original numbered files or your edited version."""

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
    retried_from: str | None = None  # this run retries that one on another local lane
    by: str = ""  # profile that started it ("owner", or a teammate's login); "" = local/CLI
    spec: dict[str, Any] | None = None  # task-derived checks, when spec.enabled
    retry_run: str | None = None  # the retry started for this run
    retry_job: str | None = None  # or the overnight job that will run it
    group: str | None = None  # runs started together by compare()
    applied_hunks: list[str] | None = None  # set when you approve only part of the diff
    repaired: bool = False  # the coder fixed its own diff after git apply failed
    fixed: bool = False  # the coder fixed its change after seeing checks it made worse
    # "" (no round) · kept · no edit blocks · didn't apply · not better
    fix_outcome: str = ""
    project: str = ""  # the project (repo folder name) the run changes
    # How a Needs-you run retries: "" the lane's config, "now" (Hybrid: deep tries it right
    # away, at your urgency), "off" (you picked one model: it answers alone).
    retry: str = ""
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


def repo_context(
    repo: Path, task: str, context_tokens: int = 10_500, home: Path | None = None
) -> str:
    """The coder's context: task, one skill card, repo map, numbered files, under a budget."""
    return build_coder_context(repo, task, budget=Budget.for_window(context_tokens), home=home)


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
        lookup: LookupClient | None = None,
        gpu: GpuScheduler | None = None,
    ) -> None:
        self.settings = settings
        # One GPU: every GPU-lane call takes the scheduler's lease (src/pablo/gpu.py).
        self.gpu = gpu or GpuScheduler(
            home=settings.home,
            idle_s=settings.gpu.idle_s,
            overnight=settings.overnight,
            default_mode=settings.gpu.mode,
            residents_cfg=settings.gpu.residents,
            unload_url=settings.gpu.unload_url,
        )
        self.backend: ChatBackend = ScheduledBackend(backend, self.gpu, off_gpu_models(settings))
        self.repo = repo.resolve()
        self.bus = bus or EventBus()
        self.checks = checks
        self.swap = swap
        # Where checks run. lanes.yaml `sandbox:` decides unless the caller picks one.
        self.sandbox = sandbox or make_sandbox(settings.sandbox, self.repo)
        self.lookup: LookupClient = lookup or ProcessLookup()
        self.baselines = Baselines()  # checks on the unedited repo, run once per repo state
        # Run `retry_on` retries in the caller's thread (evals, tests) instead of in the background.
        self.retry_inline = False
        self.router = Router(settings, self.backend)
        self.lessons = Lessons(settings.home)
        # Which repo lessons belong to. Evals set the seed's name; one dir per task otherwise.
        self.repo_key = self.repo.name
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
        retried_from: str | None = None,
        by: str = "",
        retry: str = "",
    ) -> Run:
        return self.finish(
            self.begin(
                task, lane=lane, task_id=task_id, escalate=escalate,
                escalated_from=escalated_from, group=group, retried_from=retried_from, by=by,
                retry=retry,
            )
        )  # fmt: skip

    def start_background(
        self, task: str, *, lane: str | None = None, by: str = "", retry: str = ""
    ) -> Run:
        """Route and save the run now; do the work on a thread. Events report its progress."""
        run = self.begin(task, lane=lane, by=by, retry=retry)
        if self.retry_inline:  # tests and evals: no threads
            return self.finish(run)
        threading.Thread(target=self.finish, args=(run,), name=f"run-{run.id}", daemon=True).start()
        return run

    def begin(
        self,
        task: str,
        *,
        lane: str | None = None,
        task_id: str | None = None,
        escalate: bool = False,
        escalated_from: str | None = None,
        group: str | None = None,
        retried_from: str | None = None,
        by: str = "",
        retry: str = "",
    ) -> Run:
        """Route the task and save the run as running. `finish` does the work."""
        decision = self.router.route(task, lane=ESCALATION_LANE if escalate else lane)
        reason = decision.reason
        if (
            lane is None
            and not escalate
            and decision.lane == "coder"
            and self.settings.learn.routing
            and "deep" in self.settings.lanes
        ):
            kind = detect_task_type(task)
            if self.lessons.prefer_deep(self.repo_key, kind):
                record = self.lessons.lane_record(self.repo_key, kind)
                c, d = record["coder"], record["deep"]
                decision = replace(decision, lane="deep")
                reason = (f"learned: deep got {d[0]}/{d[1]} {kind} tasks right here, "
                          f"the coder {c[0]}/{c[1]}")  # fmt: skip
        lane_cfg = self.settings.lane(decision.lane)
        if lane_cfg.requires_approval and not escalate:
            raise PermissionError(f"lane '{lane_cfg.name}' only runs when you escalate")

        run = Run(
            id=uuid.uuid4().hex[:10],
            task=task,
            lane=lane_cfg.name,
            model=lane_cfg.model,
            route_reason=reason,
            complexity=decision.complexity,
            task_id=task_id,
            escalated_from=escalated_from,
            group=group,
            retried_from=retried_from,
            by=by,
            project=self.repo.name,
            retry=retry,
        )
        self._save(run)
        self._emit("route", run, reason=reason, confidence=decision.confidence)
        return run

    def finish(self, run: Run) -> Run:
        try:
            self._work(run)
        except Exception as exc:
            # Keep the run so a suite can continue; the cockpit still shows Needs you / failed.
            run.state = "failed"
            run.question = f"Run failed: {exc}"
            self._save(run)
            self._emit("error", run, message=str(exc))
        if run.state in ("needs_you", "failed"):
            self._retry_elsewhere(run)
        return run

    def _learn(self, run: Run, *, ok: bool, whole: bool) -> None:
        """Your decision as a lesson: a right answer to show later, and who got it right."""
        lane = self.settings.lanes.get(run.lane)
        if not run.diff or lane is None or lane.kind != "local" or not lane.writes_code:
            return  # nothing from Claude, nothing without a change
        kind = detect_task_type(run.task)
        self.lessons.add_outcome(Outcome(self.repo_key, kind, run.lane, ok))
        if ok and whole and run.lane in ("coder", "deep") and lane.train_ok:
            files = list((run.verdict or {}).get("files", []))
            self.lessons.add_example(Example(self.repo_key, run.task, run.diff, run.lane,
                                             "approved", kind, files))  # fmt: skip

    def _lessons_for(self, task: str) -> str:
        """Examples and past failures from this repo, when `learn` turns them on."""
        cfg = self.settings.learn
        parts = []
        if cfg.examples:
            parts.append(self.lessons.examples_section(self.repo_key, task, cfg.example_tokens))
        if cfg.failures:
            parts.append(self.lessons.failures_section(self.repo_key, task))
        return "\n\n".join(p for p in parts if p)

    def _spec(self, run: Run, verdict: Verdict) -> SpecResult | None:
        """Write tests from the task (not the diff) and run them before and after the change."""
        cfg = self.settings.spec
        if not cfg.enabled or not self.settings.lane(run.lane).writes_code:
            return None
        writer = self.settings.lane(cfg.lane)
        try:
            context = build_coder_context(
                self.repo, run.task, budget=SPEC_BUDGET, home=self.settings.home
            )
            source = self._during_swap(
                run, cfg.lane, writer.model, "judge · writing checks from your request",
                lambda: write_spec(self.backend, writer.model, run.task, context),
            )  # fmt: skip
            if not source:
                result = SpecResult("skipped", "nothing concrete to test in the request")
            else:
                self._emit("status", run, lane=cfg.lane, text="running checks from your request")
                result = run_spec(self.repo, run.diff, source, self.checks or DEFAULT_CHECKS,
                                  self.sandbox, cfg.timeout_s)  # fmt: skip
        except Exception as exc:  # a spec check is extra evidence; it must never fail a run
            result = SpecResult("skipped", f"error: {exc}")
        run.spec = result.to_dict()
        self._emit("spec", run, status=result.status, failing=result.failing[:3])
        return result

    def _retry_elsewhere(self, run: Run) -> None:
        """A run that needs you (or failed) gets a fresh try on its `retry_on` lane (coder → deep).

        A failed coder run is often a prompt too big for its 16k window; deep has 64k.

        Only plain runs retry: not a retry, an escalation, or a compare (which already has its
        other lanes). The two share a group, so approving one rejects the other.
        """
        lane = self.settings.lane(run.lane)
        target = lane.retry_on
        if (
            target is None
            or (run.retry or lane.retry_when) == "off"
            or run.retried_from
            or run.escalated_from
            or run.group
            or (lane.retry_if == "broken" and not _broken(run))
        ):
            return
        run.group = uuid.uuid4().hex[:8]
        when = run.retry or lane.retry_when
        if when == "now" and self.gpu.mode == "interactive" and not run.retry:
            when = "overnight"  # interactive mode keeps the coder hot; deep tries it tonight
        if when == "overnight":
            from pablo.overnight import JobQueue

            job = JobQueue(self.settings.home).add(
                run.task, lane=target, task_id=run.task_id, group=run.group, retried_from=run.id
            )
            run.retry_job = job.id
            run.question = f"{run.question} {target.capitalize()} will try it tonight.".strip()
            self._save(run)
            self._emit("retry", run, lane=target, when="overnight", job=job.id)
            return
        asked = run.question
        run.question = f"{asked} {target.capitalize()} is trying it now.".strip()
        self._save(run)
        self._emit("retry", run, lane=target, when="now")

        # Hybrid asked for the retry: it runs at your urgency. A config retry waits for quiet.
        urgency = Priority.RUN if run.retry == "now" else Priority.RETRY

        def go() -> None:
            with gpu_job(urgency, run.by):  # RETRY waits for a quiet GPU in balanced mode
                retry = self.start(
                    run.task, lane=target, task_id=run.task_id, group=run.group,
                    retried_from=run.id, by=run.by,
                )  # fmt: skip
            run.retry_run = retry.id
            verdict = "is ready for you" if retry.state == "ready" else "needs you too"
            run.question = f"{asked} {target.capitalize()} tried it too ({retry.id}): {verdict}."
            run.question = run.question.strip()
            self._save(run)

        if self.retry_inline:
            go()
        else:
            # The cockpit gets the coder's result now; deep's arrives on the event stream.
            threading.Thread(target=go, name=f"retry-{run.id}", daemon=True).start()

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

    def _fix_round(
        self,
        run: Run,
        lane: Lane,
        messages: list[Message],
        reply: Completion,
        verdict: Verdict,
    ) -> tuple[list[Message], Completion, Verdict]:
        """Show the coder the checks it made worse and take its fix if the result is better."""
        failing = [c for c in verdict.checks if c.status not in ("passed", "already failing")]
        failures = "\n\n".join(f"{c.label()}\n{clip(c.output_tail, 600)}" for c in failing)
        self._emit("status", run, text=f"{run.lane} · checks got worse, fixing")
        asked: list[Message] = [
            *messages,
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": FIX_BLOCKS_PROMPT.format(failures=failures)},
        ]
        answer = self._during_swap(
            run,
            run.lane,
            run.model,
            f"{run.lane} · thinking",
            lambda: self.backend.complete(
                run.model,
                asked,
                temperature=lane.temperature,
                top_p=lane.top_p,
                max_tokens=lane.max_tokens,
                timeout_s=lane.timeout_s,
            ),
            announce=False,
        )
        if not has_blocks(answer.text):
            run.fix_outcome = "no edit blocks"
            return messages, reply, verdict
        blocks = parse_blocks(answer.text)
        try:
            diff = blocks_to_diff(self.repo, blocks)
        except EditError:
            try:
                diff = blocks_on_top(self.repo, run.diff, blocks)
            except EditError:
                run.fix_outcome = "didn't apply"
                return messages, reply, verdict
        fixed = verify_diff(
            self.repo, diff, self.checks, sandbox=self.sandbox, baselines=self.baselines
        )
        if not _better(fixed, verdict):
            self._emit("status", run, text=f"{run.lane} · fix didn't help, keeping the first try")
            run.fix_outcome = "not better"
            return messages, reply, verdict
        run.diff = diff
        run.fixed = True
        run.fix_outcome = "kept"
        run.explanation = strip_blocks(answer.text) or run.explanation
        return asked, answer, fixed

    def _complete(self, run: Run, lane: Lane, messages: list[Message]) -> Completion:
        return cast(
            Completion,
            self._during_swap(
                run,
                run.lane,
                run.model,
                f"{run.lane} · thinking",
                lambda: self.backend.complete(
                    run.model,
                    messages,
                    temperature=lane.temperature,
                    top_p=lane.top_p,
                    max_tokens=lane.max_tokens,
                    timeout_s=lane.timeout_s,
                ),
            ),
        )

    def _lookup_round(
        self, run: Run, lane: Lane, messages: list[Message]
    ) -> tuple[list[Message], Completion]:
        """Ask for symbols, fetch defs/refs in a child process, then ask for edits."""
        asked: list[Message] = [
            {"role": "system", "content": ASK_SYMBOLS_PROMPT},
            messages[1],  # the context user message
        ]
        first = self._complete(run, lane, asked)
        symbols = parse_symbol_request(first.text)
        # One-shot fallback: the model already wrote edit blocks or a diff.
        if has_blocks(first.text) or (lane.edit_format == "diff" and "```" in first.text):
            self._emit("lookup", run, skipped=True, reason="one-shot")
            edit = CODER_BLOCKS_PROMPT if lane.edit_format == "blocks" else CODER_PROMPT
            return [{"role": "system", "content": edit}, messages[1]], first
        if not symbols:
            self._emit("lookup", run, skipped=True, reason="no symbols")
            return messages, first
        self._emit("status", run, text=f"{run.lane} · looking up symbols")
        result = self.lookup.lookup(self.repo, symbols, home=self.settings.home)
        self._emit(
            "lookup",
            run,
            symbols=symbols,
            hits=len(result.hits),
            error=result.error,
        )
        edit_system = CODER_BLOCKS_PROMPT if lane.edit_format == "blocks" else CODER_PROMPT
        follow: list[Message] = [
            {"role": "system", "content": edit_system},
            messages[1],
            {"role": "assistant", "content": first.text},
            {
                "role": "user",
                "content": EDIT_WITH_LOOKUP_PROMPT.format(lookup=result.render()),
            },
        ]
        second = self._complete(run, lane, follow)
        return follow, second

    def _work(self, run: Run) -> None:
        lane = self.settings.lane(run.lane)
        wants_diff = lane.writes_code or run.lane in ("coder", ESCALATION_LANE)
        blocks = lane.edit_format == "blocks"
        system = (CODER_BLOCKS_PROMPT if blocks else CODER_PROMPT) if wants_diff else ANSWER_PROMPT
        learned = self._lessons_for(run.task) if wants_diff and lane.kind == "local" else ""
        budget = Budget.for_window(lane.context_tokens).shrunk(tokens(learned))
        repo_text = build_coder_context(self.repo, run.task, budget=budget, home=self.settings.home)
        messages: list[Message] = [
            {"role": "system", "content": system},
            {"role": "user", "content": repo_text + (f"\n\n{learned}" if learned else "")},
        ]
        shown = context_files(self.repo, run.task, budget, home=self.settings.home)
        self._emit("context", run, files=shown, budget_tokens=budget.total)
        if wants_diff and lane.symbol_lookup:
            messages, reply = self._lookup_round(run, lane, messages)
        else:
            reply = self._complete(run, lane, messages)
        run.explanation, run.diff, edit_error = self._extract(reply.text)
        if not wants_diff:
            # A talk-only lane (the advisor) never hands back a change, even if it writes blocks.
            run.explanation, run.diff, edit_error = reply.text, "", ""
        verdict: Verdict | None = None
        if run.diff:
            self._emit("status", run, text=f"{run.lane} · running checks")
            verdict = verify_diff(
                self.repo, run.diff, self.checks, sandbox=self.sandbox, baselines=self.baselines
            )

        # Local models slip on exact text. Give the coder one chance to fix its own edits,
        # with the precise error, before anything is judged.
        problem = edit_error or (verdict.error if verdict and not verdict.applied else "")
        if wants_diff and problem:
            self._emit("status", run, text=f"{run.lane} · edits didn't apply, repairing")
            repair = REPAIR_BLOCKS_PROMPT if blocks else REPAIR_PROMPT
            # Keep repair short: system + original context + last reply + error.
            # Dropping the lookup turn avoids blowing the 16k window on harness tasks.
            context = messages[1] if len(messages) > 1 else messages[0]
            if unseen_files(self.repo, reply.text, shown, budget.per_file):
                # The full context + the reply + an unseen file overflows the coder's 16k
                # window (llama.cpp answers 400 and the run fails). The repair only needs the
                # files its edits touch, so show those instead of the original context.
                context = {
                    "role": "user",
                    "content": repair_context(self.repo, run.task, reply.text, budget),
                }
            messages = [
                {"role": "system", "content": system},
                context,
                {"role": "assistant", "content": reply.text},
                {"role": "user", "content": repair.format(error=problem)},
            ]
            reply = self._complete(run, lane, messages)
            explanation, repaired, edit_error = self._extract(reply.text)
            if repaired:
                run.explanation = explanation or run.explanation
                run.diff = repaired
                run.repaired = True
                verdict = verify_diff(
                    self.repo, run.diff, self.checks, sandbox=self.sandbox, baselines=self.baselines
                )

        if wants_diff and run.diff and verdict is not None and verdict.applied:
            for _ in range(lane.fix_rounds if lane.kind == "local" else 0):
                if verdict.clean:
                    break
                messages, reply, verdict = self._fix_round(run, lane, messages, reply, verdict)

        if run.diff and verdict is not None:
            checked = verdict
            run.verdict = checked.to_dict()
            self._emit(
                "evidence",
                run,
                summary=checked.summary(),
                passed=checked.passed,
                clean=checked.clean,
            )

            if not checked.clean and lane.kind == "local":
                worse = next(
                    (c for c in checked.checks if c.status not in ("passed", "already failing")),
                    None,
                )
                error = first_error(worse.output_tail) if worse else (checked.error or "")
                self.lessons.add_failure(Failure(self.repo_key, run.task, run.lane, error,
                                                 list(checked.files)))  # fmt: skip
            judge_model = self.settings.lane("judge").model
            spec = self._spec(run, checked) if checked.clean else None
            extra = spec.evidence() if spec else ""
            judgement = self._during_swap(
                run,
                "judge",
                judge_model,
                "judge · reviewing",
                lambda: judge(self.backend, judge_model, run.task, run.diff, checked, extra),
            )
            if spec and spec.status == "failed":
                # Your own words, as a test, fail on this change: you decide, not the judge.
                judgement = Judgement(
                    min(judgement.certainty, SPEC_CAP),
                    judgement.summary,
                    "A check written from your request fails on this change: "
                    + (spec.failing[0] if spec.failing else spec.reason)
                    + ". Is the request or the change right?",
                )
            run.certainty = judgement.certainty
            run.judge_summary = judgement.summary
            run.question = judgement.question
            ready = (
                verdict.clean and judgement.certainty >= self.settings.thresholds.judge_needs_you
            )
            run.state = "ready" if ready else "needs_you"
            self._emit("judged", run, certainty=judgement.certainty, state=run.state)
        elif wants_diff and edit_error:
            run.state = "needs_you"
            run.question = (
                f"The coder's edits could not be applied ({edit_error}). Retry or escalate?"
            )
        elif wants_diff:
            # A code lane that replied without a change has not done the task.
            run.state = "needs_you"
            run.question = (
                "The coder replied without a change. Read its answer, retry, or escalate?"
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
                verdict = verify_diff(
                    self.repo, patch, self.checks, sandbox=self.sandbox, baselines=self.baselines
                )
                if not verdict.clean and not force:
                    raise PartialChecksFailed(verdict)
                run.applied_hunks = [h for h in every if h in keep]
        if patch:
            ok, err = apply_diff(self.repo, patch)
            if not ok:
                raise RuntimeError(f"diff no longer applies: {err}")
        run.state = "approved"
        self.traces.record_decision(run.id, decision)
        self._save(run)
        self._learn(run, ok=True, whole=decision == "approved")
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
        self._learn(run, ok=False, whole=False)
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


def _better(new: Verdict, old: Verdict) -> bool:
    """Clean beats not clean; otherwise fewer checks worse than the unedited repo."""
    if not new.applied:
        return False
    if new.clean != old.clean:
        return new.clean

    def bad(v: Verdict) -> int:
        return sum(c.status not in ("passed", "already failing") for c in v.checks)

    return bad(new) < bad(old)


# Spec writers get the files the task names, small: they test the request, not the repo.
SPEC_BUDGET = Budget(total=5000, files=3500, tree=500, max_files=4)


def _broken(run: Run) -> bool:
    """The change itself is bad: the run failed, made no diff, didn't apply, or checks got worse.

    A clean change the judge was merely unsure about is not broken; you read it, deep doesn't.
    """
    if run.state == "failed" or not run.diff or not run.verdict:
        return True
    return not (run.verdict.get("applied", True) and run.verdict.get("clean", False))


def _edited_paths(repo: Path, reply: str) -> list[str]:
    """Files a reply's edit blocks touch that exist in the repo (sorted, never outside it)."""
    if not has_blocks(reply):
        return []
    try:
        paths = {b.path for b in parse_blocks(reply)}
    except EditError:
        return []
    return sorted(
        rel for rel in paths if rel and ".." not in Path(rel).parts and (repo / rel).is_file()
    )


def repair_context(repo: Path, task: str, reply: str, budget: Budget) -> str:
    """A small repair prompt: the task and the files the reply edits, numbered.

    Used when the reply edited a file its prompt never showed, so the repair fits the same
    budget as the first prompt instead of stacking a new file on top of it.
    """
    paths = _edited_paths(repo, reply)
    per_file = max(1500, budget.files // max(1, len(paths)))
    blocks = []
    for rel in paths:
        body = (repo / rel).read_text(encoding="utf-8", errors="replace")
        blocks.append(f"--- {rel}\n{clip(numbered(body), per_file)}")
    return (
        f"Task:\n{clip(task, 1000)}\n\n"
        "The files your edits touch, as they are now (numbered):\n\n" + "\n\n".join(blocks)
    )


def unseen_files(repo: Path, reply: str, shown: list[str], max_tokens: int) -> str:
    """The files a reply edited that its prompt never showed, numbered, for the repair turn.

    A SEARCH block for an unseen file is written from memory and rarely matches; showing the
    file once is what lets the repair land (M6: harness-clip-small-caps missed in every run).
    """
    parts = []
    for rel in _edited_paths(repo, reply):
        if rel not in shown:
            body = (repo / rel).read_text(encoding="utf-8", errors="replace")
            parts.append(f"--- {rel}\n{clip(numbered(body), max_tokens)}")
    if not parts:
        return ""
    return "\n\nYou edited files you were not shown. Here they are as they are now:\n\n" + (
        "\n\n".join(parts)
    )
