"""A tiny pub/sub so the cockpit, TUI, and phone see the same live status line."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Event:
    kind: str  # "route" | "status" | "evidence" | "judged" | "needs_you" | "decided" | "error"
    run_id: str
    data: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "run_id": self.run_id, "data": self.data, "at": self.at}


class EventBus:
    def __init__(self, history: int = 500) -> None:
        self._history: deque[Event] = deque(maxlen=history)
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]] = []
        self._lock = threading.Lock()

    def publish(self, event: Event) -> None:
        with self._lock:
            self._history.append(event)
            subs = list(self._subs)
        for loop, queue in subs:
            if loop.is_closed():
                continue
            loop.call_soon_threadsafe(queue.put_nowait, event)

    def history(self, run_id: str | None = None) -> list[Event]:
        with self._lock:
            return [e for e in self._history if run_id is None or e.run_id == run_id]

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subs = [(lp, q) for lp, q in self._subs if q is not queue]
