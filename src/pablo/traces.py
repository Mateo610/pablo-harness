"""Append-only traces: every call, its evidence, and what you decided.

One JSONL file per day under ~/.pablo/traces. Traces feed the learning loop, so
the training rule is enforced here, not left to whoever writes the export:
a trace is trainable only if its lane allows it AND you approved the result.
Escalation (Claude) traces are never trainable.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pablo.config import ESCALATION_LANE


@dataclass
class Trace:
    run_id: str
    lane: str
    model: str
    messages: list[dict[str, str]]
    output: str
    lane_train_ok: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    verdict: dict[str, Any] | None = None
    certainty: float | None = None
    decision: str | None = None  # "approved" | "rejected" | None (pending)
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def train_ok(self) -> bool:
        if self.lane == ESCALATION_LANE or not self.lane_train_ok:
            return False
        return self.decision == "approved"

    @property
    def prompt_hash(self) -> str:
        raw = json.dumps(self.messages, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["train_ok"] = self.train_ok
        data["prompt_hash"] = self.prompt_hash
        return data


class TraceStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def _file_for(self, at: str) -> Path:
        return self.root / f"{at[:10].replace('-', '')}.jsonl"

    def append(self, trace: Trace) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(trace.to_dict(), ensure_ascii=False)
        with self._lock, self._file_for(trace.at).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def record_decision(self, run_id: str, decision: str) -> None:
        """Decisions are appended as events; readers fold them onto traces."""
        self.root.mkdir(parents=True, exist_ok=True)
        event = {
            "event": "decision",
            "run_id": run_id,
            "decision": decision,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        with self._lock, self._file_for(event["at"]).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")

    def iter_traces(self) -> Iterator[Trace]:
        decisions: dict[str, str] = {}
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("event") == "decision":
                    decisions[row["run_id"]] = row["decision"]
                else:
                    rows.append(row)
        for row in rows:
            row.pop("train_ok", None)
            row.pop("prompt_hash", None)
            trace = Trace(**row)
            if trace.decision is None and trace.run_id in decisions:
                trace.decision = decisions[trace.run_id]
            yield trace

    def export_training(self, out: Path) -> int:
        """Write approved, trainable traces as chat-format JSONL. Returns the count."""
        count = 0
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for trace in self.iter_traces():
                if not trace.train_ok:
                    continue
                convo = [*trace.messages, {"role": "assistant", "content": trace.output}]
                fh.write(json.dumps({"messages": convo, "lane": trace.lane}) + "\n")
                count += 1
        return count
