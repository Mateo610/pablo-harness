"""Small HTTP client for the harness API, shared by the TUI and scripts."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Protocol

import httpx

Json = dict[str, Any]


class HarnessAPI(Protocol):
    def health(self) -> Json: ...
    def plan(self) -> Json | None: ...
    def inbox(self) -> list[Json]: ...
    def runs(self, limit: int = 30) -> list[Json]: ...
    def start(self, task: str, lane: str | None = None, task_id: str | None = None) -> Json: ...
    def decide(self, run_id: str, decision: str) -> Json: ...
    def events(self) -> Iterator[Json]: ...


class HarnessError(RuntimeError):
    pass


class HarnessClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8710", timeout_s: float = 900) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout_s)

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise HarnessError(f"can't reach Pablo at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise HarnessError(str(detail))
        return resp.json()

    def health(self) -> Json:
        return dict(self._call("GET", "/api/health"))

    def plan(self) -> Json | None:
        try:
            return dict(self._call("GET", "/api/plan"))
        except HarnessError:
            return None

    def inbox(self) -> list[Json]:
        return list(self._call("GET", "/api/inbox"))

    def runs(self, limit: int = 30) -> list[Json]:
        return list(self._call("GET", "/api/runs", params={"limit": limit}))

    def start(self, task: str, lane: str | None = None, task_id: str | None = None) -> Json:
        body = {"task": task, "lane": lane, "task_id": task_id}
        return dict(self._call("POST", "/api/runs", json=body))

    def decide(self, run_id: str, decision: str) -> Json:
        if decision not in ("approve", "reject", "escalate"):
            raise ValueError(decision)
        return dict(self._call("POST", f"/api/runs/{run_id}/{decision}"))

    def events(self) -> Iterator[Json]:
        """Blocking iterator over server-sent events; reconnect is the caller's job."""
        with (
            httpx.Client(base_url=self.base_url, timeout=None) as http,
            http.stream("GET", "/api/events") as resp,
        ):
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    yield dict(json.loads(line[6:]))
