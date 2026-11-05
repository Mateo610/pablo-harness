"""One GPU, many people: who gets the model next.

Every model call on a GPU lane takes a lease first. Calls for the model that's already loaded
share it (llama-server queues them). A call for a different model waits until the GPU is free,
because loading it evicts the current one, and a deep load can take the GPU for minutes.

Who goes first, most urgent first:

    voice   a live call: someone is waiting to hear an answer
    chat    a chat reply
    run     a run someone started (chat "do it", the Run button, the CLI)
    retry   a coder run that failed, retried on deep
    night   the overnight queue and practice

Same urgency: the person served longest ago goes first, then the model already loaded.

Modes (owner's switch on Home):
    interactive   keep the coder hot: retries move to tonight's queue; night work waits for night
    balanced      background work (retry, night) starts after `idle_s` with no live activity
    deep          deep work any time; voice is paused (it would evict deep)

A lease can't stop a generation that has started; it decides what starts next.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Literal

from pablo.config import Overnight, Settings
from pablo.gateway import ChatBackend, Completion, Delta, Message

Mode = Literal["interactive", "balanced", "deep"]
MODES: tuple[Mode, ...] = ("interactive", "balanced", "deep")


class Priority(IntEnum):
    VOICE = 0
    CHAT = 1
    RUN = 2
    RETRY = 3
    NIGHT = 4


LIVE = Priority.RUN  # at or above this urgency counts as someone actively working
_job: ContextVar[tuple[Priority, str]] = ContextVar("pablo_gpu_job", default=(Priority.RUN, ""))


@contextmanager
def job(priority: Priority, who: str = "") -> Iterator[None]:
    """Mark the model calls made inside this block (this thread) with an urgency and a person."""
    token = _job.set((priority, who))
    try:
        yield
    finally:
        _job.reset(token)


def current_job() -> tuple[Priority, str]:
    return _job.get()


@dataclass
class Lease:
    seq: int
    model: str
    priority: Priority
    who: str
    since: float

    def view(self, now: float) -> dict[str, Any]:
        return {"model": self.model, "priority": self.priority.name.lower(), "who": self.who,
                "for_s": round(now - self.since, 1)}  # fmt: skip


@dataclass
class GpuScheduler:
    home: Path
    idle_s: float = 120.0
    overnight: Overnight = field(default_factory=Overnight)
    default_mode: Mode = "balanced"
    clock: Callable[[], float] = time.monotonic
    local_now: Callable[[], datetime] = datetime.now

    def __post_init__(self) -> None:
        self._cond = threading.Condition()
        self._seq = itertools.count()
        self._holders: list[Lease] = []
        self._waiting: list[Lease] = []
        self._resident: str | None = None
        self._last_live = float("-inf")
        self._served: dict[str, float] = {}
        self._mode: Mode = self._load_mode()

    # mode -----------------------------------------------------------------------------------

    def _mode_file(self) -> Path:
        return self.home / "gpu-mode.json"

    def _load_mode(self) -> Mode:
        try:
            mode = json.loads(self._mode_file().read_text())["mode"]
            return mode if mode in MODES else self.default_mode
        except (OSError, ValueError, KeyError, TypeError):
            return self.default_mode

    @property
    def mode(self) -> Mode:
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        with self._cond:
            self._mode = mode
            self.home.mkdir(parents=True, exist_ok=True)
            self._mode_file().write_text(json.dumps({"mode": mode}))
            self._cond.notify_all()

    # rules ----------------------------------------------------------------------------------

    def _night(self) -> bool:
        from pablo.overnight import in_window

        return in_window(self.overnight, self.local_now())

    def background_ok(self, priority: Priority) -> bool:
        """May background work (retry, night) start now? Holds the lock or reads a snapshot."""
        if priority < Priority.RETRY:
            return True
        if self._mode == "deep":
            return True
        if self._mode == "interactive":
            return priority == Priority.NIGHT and self._night()
        live_waiting = any(w.priority <= LIVE for w in self._waiting)
        quiet = self.clock() - self._last_live >= self.idle_s
        return (quiet and not live_waiting) or (priority == Priority.NIGHT and self._night())

    def _order(self, lease: Lease) -> tuple[int, float, int, int]:
        return (
            int(lease.priority),
            self._served.get(lease.who, float("-inf")),
            0 if lease.model == self._resident else 1,
            lease.seq,
        )

    def _may_start(self, req: Lease) -> bool:
        if not self.background_ok(req.priority):
            return False
        ahead = [
            w
            for w in self._waiting
            if w is not req and self._order(w) < self._order(req) and self.background_ok(w.priority)
        ]
        if self._holders:
            # Join the loaded model unless someone more urgent needs a different one.
            return self._resident == req.model and not any(w.model != req.model for w in ahead)
        return not ahead

    # lease ----------------------------------------------------------------------------------

    @contextmanager
    def lease(self, model: str) -> Iterator[Lease]:
        priority, who = current_job()
        req = Lease(next(self._seq), model, priority, who, self.clock())
        with self._cond:
            self._waiting.append(req)
            if priority <= LIVE:
                self._last_live = self.clock()
            while not self._may_start(req):
                self._cond.wait(timeout=1.0)  # idle and night rules change with time
            self._waiting.remove(req)
            self._holders.append(req)
            self._resident = model
            self._served[who] = self.clock()
            self._cond.notify_all()
        try:
            yield req
        finally:
            with self._cond:
                self._holders.remove(req)
                if priority <= LIVE:
                    self._last_live = self.clock()
                self._cond.notify_all()

    def poke(self) -> None:
        """Re-check who may start now (after the clock or the night window moved)."""
        with self._cond:
            self._cond.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._cond:
            now = self.clock()
            idle = now - self._last_live
            return {
                "mode": self._mode,
                "resident": self._resident,
                "holders": [h.view(now) for h in self._holders],
                "waiting": [w.view(now) for w in sorted(self._waiting, key=self._order)],
                "idle_s": None if idle == float("inf") else round(idle, 1),
                "background_ok": self.background_ok(Priority.RETRY),
                "idle_needed_s": self.idle_s,
            }


class ScheduledBackend:
    """A ChatBackend whose GPU-lane calls go through the scheduler. Everything else passes."""

    def __init__(self, inner: ChatBackend, gpu: GpuScheduler, gpu_models: Sequence[str]) -> None:
        self.inner, self.gpu = inner, gpu
        self.cpu_or_remote = set(gpu_models)  # models that never take the GPU lease

    def __getattr__(self, name: str) -> Any:  # tests reach the fake's .calls and .scripts
        return getattr(self.inner, name)

    @contextmanager
    def _maybe_lease(self, model: str) -> Iterator[None]:
        if model in self.cpu_or_remote:
            yield
            return
        with self.gpu.lease(model):
            yield

    def complete(self, model: str, messages: Sequence[Message], **kw: Any) -> Completion:
        with self._maybe_lease(model):
            return self.inner.complete(model, messages, **kw)

    def stream(
        self, model: str, messages: Sequence[Message], *, on_delta: Delta, **kw: Any
    ) -> Completion:
        with self._maybe_lease(model):
            stream = getattr(self.inner, "stream", None)
            if stream is None:
                done = self.inner.complete(model, messages, **kw)
                on_delta(done.text)
                return done
            result: Completion = stream(model, messages, on_delta=on_delta, **kw)
            return result


def off_gpu_models(settings: Settings) -> list[str]:
    """Models that don't take the GPU lease: CPU lanes (the router) and remote lanes."""
    return [lane.model for lane in settings.lanes.values() if lane.kind != "local" or not lane.gpu]
