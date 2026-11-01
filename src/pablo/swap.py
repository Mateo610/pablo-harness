"""What llama-swap has resident.

Pablo does not start or stop models. llama-swap on 127.0.0.1:8100 does that
when a request names a lane (`router`, `coder`, `judge`, `deep`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

DEFAULT_BASE = "http://127.0.0.1:8100"


def model_id(alias: str) -> str:
    """`pablo/judge` is the swapper's model id `judge`."""
    return alias.rsplit("/", 1)[-1]


def parse_running(payload: Any) -> dict[str, str]:
    """Model id to state. A name with no state counts as ready.

    llama-swap has shipped `/running` as a list of names, one `{model, state}`
    object, and a list of process objects. All three are accepted.
    """
    if not isinstance(payload, dict) or not payload:
        return {}
    if "running" in payload:
        return _from_running(payload["running"])
    model = payload.get("model")
    if isinstance(model, str) and "state" in payload and not _nested(payload):
        state = payload.get("state")
        return {model: str(state) if state else "ready"}
    states: dict[str, str] = {}
    for key, value in payload.items():
        parsed = _named(key, value)
        if parsed is not None:
            states[parsed[0]] = parsed[1]
    return states


def swap_times(samples: list[tuple[float, dict[str, str]]]) -> tuple[list[float], list[float]]:
    """Seconds spent swapping coder to judge, then judge back to coder.

    A sample is `(time, model id to state)`. The span starts when the resident
    GPU model is no longer the only ready one, and ends when the other is ready.
    A sample that jumps straight from one ready model to the other uses the
    previous sample's time as the start.
    """
    to_judge: list[float] = []
    to_coder: list[float] = []
    resident: str | None = None
    left: str | None = None
    left_at: float | None = None
    prev_t: float | None = None
    for now, states in samples:
        ready = {name for name in ("coder", "judge") if states.get(name) == "ready"}
        current = next(iter(ready)) if len(ready) == 1 else None
        if resident is not None and current != resident and left is None:
            left = resident
            left_at = now if current is None else prev_t
        if left is not None and current is not None and current != left and left_at is not None:
            span = now - left_at
            if left == "coder" and current == "judge":
                to_judge.append(span)
            elif left == "judge" and current == "coder":
                to_coder.append(span)
            left, left_at = None, None
        if current is not None:
            resident = current
        prev_t = now
    return to_judge, to_coder


def loaded_ids(payload: Any) -> list[str]:
    """Models the swapper reports as ready, or listed with no state."""
    return sorted(name for name, state in parse_running(payload).items() if state in ("ready", ""))


def read_running(base_url: str = DEFAULT_BASE) -> Any:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/running", timeout=2.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OSError(str(exc)) from exc
    return response.json()


class SwapView:
    """One look at the swapper. A failed read is `unknown`, which is not a swap."""

    def __init__(self, reader: Callable[[], Any] | None = None) -> None:
        self._reader = reader or read_running

    def state(self, model: str) -> str:
        try:
            states = parse_running(self._reader())
        except (OSError, ValueError, TypeError):
            return "unknown"
        return states.get(model, "absent")


def _nested(payload: dict[str, Any]) -> bool:
    return any(isinstance(value, (dict, list)) for value in payload.values())


def _from_running(running: Any) -> dict[str, str]:
    if isinstance(running, dict):
        return parse_running(running)
    if not isinstance(running, list):
        return {}
    states: dict[str, str] = {}
    for item in running:
        if isinstance(item, str) and item:
            states[item] = "ready"
        elif isinstance(item, dict):
            name = str(item.get("model") or item.get("id") or item.get("name") or "")
            if not name:
                continue
            state = item.get("state")
            states[name] = str(state) if state else "ready"
    return states


def _named(key: str, value: Any) -> tuple[str, str] | None:
    if isinstance(value, str):
        return key, value or "ready"
    if isinstance(value, dict):
        state = value.get("state")
        return str(value.get("model") or key), str(state) if state else "ready"
    return None
