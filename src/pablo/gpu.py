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

Residents: processes that live on the GPU outside llama-swap (the TTS server). One that can't
share the GPU with a model (TTS with deep) is paused before that model loads, by whichever Pablo
process loads it, and woken when someone needs it (a voice turn), after unloading the idle model.

Modes (owner's switch on Home; a file, so every Pablo process sees the same mode):
    interactive   keep the coder hot: retries move to tonight's queue; night work waits for night
    balanced      background work (retry, night) starts after `idle_s` with no live activity
    deep          deep work any time; voice is paused (it would evict deep)

Every Pablo process on the box (`pablo serve`, a CLI eval, a practice run) shares one lease
table, `gpu-leases.json` in the shared folder (the real ~/.pablo, even for an eval in a throwaway
home), under a file lock. A process may start a model only if no other process holds a
different one and no more urgent caller elsewhere waits for a different one. Entries from
processes that died are ignored.

A lease can't stop a generation that has started; it decides what starts next.
"""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import shlex
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Literal

from pablo.config import Overnight, ResidentConfig, Settings
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
    wall: float = field(default_factory=time.time)  # for ordering against other processes

    def view(self, now: float) -> dict[str, Any]:
        return {"model": self.model, "priority": self.priority.name.lower(), "who": self.who,
                "for_s": round(now - self.since, 1)}  # fmt: skip


class ResidentError(RuntimeError):
    pass


WAITER_STALE_S = 15.0  # a waiter re-publishes every half second; older means it's gone
HOLDER_STALE_S = 3 * 3600.0  # deep's longest call is an hour


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SharedTable:
    """The lease table every Pablo process on this machine reads and writes."""

    def __init__(self, folder: Path, owner: str = "") -> None:
        self.folder = folder
        self.owner = owner or uuid.uuid4().hex[:12]
        self.path = folder / "gpu-leases.json"

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / "gpu-leases.lock").open("a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                try:
                    data = json.loads(self.path.read_text("utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                except (OSError, ValueError):
                    data = {}
                data.setdefault("entries", [])
                yield data
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    @staticmethod
    def _live(entry: dict[str, Any], now: float) -> bool:
        if not _alive(int(entry.get("pid", 0))):
            return False
        age = now - float(entry.get("beat", 0))
        return age < (HOLDER_STALE_S if entry.get("state") == "holding" else WAITER_STALE_S)

    def publish(self, mine: list[dict[str, Any]], last: str | None = None) -> None:
        now = time.time()
        with self._locked() as data:
            keep = [e for e in data["entries"]
                    if e.get("owner") != self.owner and self._live(e, now)]  # fmt: skip
            for e in mine:
                keep.append({**e, "owner": self.owner, "pid": os.getpid(), "beat": now})
            data["entries"] = keep
            if last is not None:
                data["last"] = last
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), "utf-8")
            tmp.replace(self.path)

    def others(self) -> tuple[list[dict[str, Any]], str | None]:
        """Live entries from every other process, and the last model anyone was given."""
        now = time.time()
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return [], None
        if not isinstance(data, dict):
            return [], None
        entries = [e for e in data.get("entries", [])
                   if e.get("owner") != self.owner and self._live(e, now)]  # fmt: skip
        last = data.get("last")
        return entries, last if isinstance(last, str) else None


def run_command(command: str, timeout_s: float = 60) -> None:
    done = subprocess.run(shlex.split(command), capture_output=True, text=True, timeout=timeout_s)
    if done.returncode != 0:
        raise ResidentError(f"{command}: {done.stderr.strip() or done.returncode}")


def http_up(url: str) -> bool:
    import httpx

    try:
        httpx.get(url, timeout=2)
        return True
    except httpx.HTTPError:
        return False


def http_post(url: str) -> None:
    import httpx

    # llama-swap's `/unload` drops every loaded model; versions differ on GET or POST.
    if httpx.get(url, timeout=30).status_code >= 400:
        httpx.post(url, timeout=30).raise_for_status()


@dataclass
class Resident:
    cfg: ResidentConfig
    state: Literal["unknown", "running", "paused", "starting"] = "unknown"
    paused_for: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class GpuScheduler:
    home: Path
    idle_s: float = 120.0
    overnight: Overnight = field(default_factory=Overnight)
    default_mode: Mode = "balanced"
    clock: Callable[[], float] = time.monotonic
    local_now: Callable[[], datetime] = datetime.now
    residents_cfg: list[ResidentConfig] = field(default_factory=list)
    unload_url: str = ""
    run: Callable[[str], None] = run_command
    up: Callable[[str], bool] = http_up
    unload: Callable[[str], None] = http_post
    sleep: Callable[[float], None] = time.sleep
    # Where the lease table every process shares lives (the real home); None: `home`.
    shared_dir: Path | None = None
    shared: bool = True

    def __post_init__(self) -> None:
        self.table = SharedTable(self.shared_dir or self.home)
        self._cond = threading.Condition()
        self._seq = itertools.count()
        self._holders: list[Lease] = []
        self._waiting: list[Lease] = []
        self._resident: str | None = None
        self._last_live = float("-inf")
        self._served: dict[str, float] = {}
        self._mode: Mode = self._load_mode()
        self.residents = {r.name: Resident(r) for r in self.residents_cfg}

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
        """Read from the file each time (it's tiny): another process (a CLI eval) may set it."""
        self._mode = self._load_mode() if self._mode_file().is_file() else self._mode
        return self._mode

    def set_mode(self, mode: Mode) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        with self._cond:
            self._mode = mode
            self.home.mkdir(parents=True, exist_ok=True)
            self._mode_file().write_text(json.dumps({"mode": mode}))
            self._cond.notify_all()

    @contextmanager
    def mode_for(self, mode: Mode) -> Iterator[None]:
        """Hold a mode for a while (a CLI eval holds Deep Work), then put the old one back."""
        before = self.mode
        self.set_mode(mode)
        try:
            yield
        finally:
            self.set_mode(before)

    # rules ----------------------------------------------------------------------------------

    def _night(self) -> bool:
        from pablo.overnight import in_window

        return in_window(self.overnight, self.local_now())

    def background_ok(self, priority: Priority) -> bool:
        """May background work (retry, night) start now? Holds the lock or reads a snapshot."""
        if priority < Priority.RETRY:
            return True
        mode = self.mode
        if mode == "deep":
            return True
        if mode == "interactive":
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
            if self._resident != req.model or any(w.model != req.model for w in ahead):
                return False
        elif ahead:
            return False
        return self._peers_allow(req)

    # other processes ------------------------------------------------------------------------

    def _entries(self) -> list[dict[str, Any]]:
        def row(lease: Lease, state: str) -> dict[str, Any]:
            return {"model": lease.model, "priority": int(lease.priority), "who": lease.who,
                    "since": lease.wall, "state": state}  # fmt: skip

        return [row(h, "holding") for h in self._holders] + [
            row(w, "waiting") for w in self._waiting
        ]

    def _publish(self, last: str | None = None) -> None:
        if self.shared:
            with suppress(OSError):  # a read-only or full disk mustn't stop local work
                self.table.publish(self._entries(), last)

    def _peers_allow(self, req: Lease) -> bool:
        """May `req` start, given what other Pablo processes hold and wait for?"""
        if not self.shared:
            return True
        others, _ = self.table.others()
        if any(e["state"] == "holding" and e["model"] != req.model for e in others):
            return False  # another process has a different model on the GPU
        mine = (int(req.priority), req.wall)
        return not any(
            e["state"] == "waiting"
            and e["model"] != req.model
            and (int(e["priority"]), float(e["since"])) < mine
            for e in others
        )

    def _peers_hold(self, models: Sequence[str]) -> bool:
        if not self.shared:
            return False
        others, _ = self.table.others()
        return any(e["state"] == "holding" and e["model"] in models for e in others)

    # lease ----------------------------------------------------------------------------------

    @contextmanager
    def lease(self, model: str) -> Iterator[Lease]:
        priority, who = current_job()
        req = Lease(next(self._seq), model, priority, who, self.clock())
        with self._cond:
            self._waiting.append(req)
            if priority <= LIVE:
                self._last_live = self.clock()
            self._publish()
            while not self._may_start(req):
                # Idle and night rules change with time, and other processes don't notify us.
                self._cond.wait(timeout=0.5)
                self._publish()  # still waiting: keep our entry fresh
            self._waiting.remove(req)
            self._holders.append(req)
            self._resident = model
            self._served[who] = self.clock()
            self._publish(last=model)
            self._cond.notify_all()
        try:
            self._pause_for(model)
            yield req
        finally:
            with self._cond:
                self._holders.remove(req)
                if priority <= LIVE:
                    self._last_live = self.clock()
                self._publish()
                self._cond.notify_all()

    # residents ------------------------------------------------------------------------------

    def _pause_for(self, model: str) -> None:
        """Before `model` runs: pause every resident that can't share the GPU with it."""
        for r in self.residents.values():
            if model not in r.cfg.conflicts:
                continue
            with r.lock:
                if r.state != "paused":
                    self.run(r.cfg.stop)  # idempotent: stopping a stopped service is fine
                    r.state, r.paused_for = "paused", model

    def ensure_resident(self, name: str, on_wake: Callable[[], None] | None = None) -> bool:
        """Make sure `name` is up (a voice turn calls this). True if it had to wake it.

        Waits while a conflicting model is working here, unloads it if it's only idle in
        VRAM, starts the resident, and waits for its health URL.
        """
        r = self.residents.get(name)
        if r is None:
            return False
        with self._cond:
            self._last_live = self.clock()  # someone is talking: background work holds off
        with r.lock:
            if self.up(r.cfg.health):
                r.state = "running"
                return False
            if on_wake:
                on_wake()
            with self._cond:
                while any(h.model in r.cfg.conflicts for h in self._holders) or self._peers_hold(
                    r.cfg.conflicts
                ):
                    self._cond.wait(timeout=0.5)
                _, last = self.table.others() if self.shared else ([], None)
                idle_conflict = self._resident in r.cfg.conflicts or last in r.cfg.conflicts
                if idle_conflict:
                    self._resident = None
            if idle_conflict and self.unload_url:
                self.unload(self.unload_url)
            r.state = "starting"
            self.run(r.cfg.start)
            waited = 0.0
            while not self.up(r.cfg.health):
                if waited >= r.cfg.warmup_s:
                    r.state = "unknown"
                    raise ResidentError(f"{name} didn't come up in {r.cfg.warmup_s:.0f} s")
                self.sleep(1.0)
                waited += 1.0
            r.state, r.paused_for = "running", ""
            return True

    def poke(self) -> None:
        """Re-check who may start now (after the clock or the night window moved)."""
        with self._cond:
            self._cond.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._cond:
            now = self.clock()
            idle = now - self._last_live
            return {
                "mode": self.mode,
                "resident": self._resident,
                "holders": [h.view(now) for h in self._holders],
                "waiting": [w.view(now) for w in sorted(self._waiting, key=self._order)],
                "idle_s": None if idle == float("inf") else round(idle, 1),
                "background_ok": self.background_ok(Priority.RETRY),
                "idle_needed_s": self.idle_s,
                "residents": [
                    {"name": n, "state": r.state, "paused_for": r.paused_for}
                    for n, r in self.residents.items()
                ],
                # Other Pablo processes (a CLI eval, say): what they hold and wait for.
                "elsewhere": [
                    {
                        "model": e["model"],
                        "state": e["state"],
                        "who": e.get("who", ""),
                        "priority": Priority(int(e["priority"])).name.lower(),
                    }
                    for e in (self.table.others()[0] if self.shared else [])
                ],
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
