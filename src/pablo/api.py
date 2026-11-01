"""Harness API: the one surface the web cockpit, TUI, and phone all talk to.

Bind it to 127.0.0.1 or your Tailscale IP only. Never expose it publicly; it can
write to your repo when you approve.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pablo import __version__
from pablo.orchestrator import Orchestrator
from pablo.plan import Plan, Status


class TaskIn(BaseModel):
    task: str
    lane: str | None = None
    task_id: str | None = None


class StatusIn(BaseModel):
    status: Status


def create_app(orch: Orchestrator) -> FastAPI:
    app = FastAPI(title="Pablo harness", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    repo: Path = orch.repo

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "repo": str(repo)}

    @app.get("/api/lanes")
    def lanes() -> list[dict[str, Any]]:
        return [lane.model_dump() for lane in orch.settings.lanes.values()]

    @app.get("/api/plan")
    def plan() -> dict[str, Any]:
        try:
            return Plan.load(repo).model_dump(mode="json")
        except FileNotFoundError as exc:
            raise HTTPException(404, "no .pablo/plan.yaml; run `pablo init`") from exc

    @app.post("/api/plan/tasks/{task_id}")
    def set_task_status(task_id: str, body: StatusIn) -> dict[str, Any]:
        current = Plan.load(repo)
        try:
            task = current.set_status(task_id, body.status)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        current.save(repo)
        return task.model_dump()

    @app.post("/api/runs")
    async def start_run(body: TaskIn) -> dict[str, Any]:
        try:
            run = await run_in_threadpool(
                orch.start, body.task, lane=body.lane, task_id=body.task_id
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        return run.to_dict()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        try:
            return orch.get(run_id).to_dict()
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc

    @app.get("/api/inbox")
    def inbox() -> list[dict[str, Any]]:
        items = sorted(orch.inbox(), key=lambda r: (r.state != "needs_you", r.created_at))
        return [r.to_dict() for r in items]

    def _decide(fn_name: str, run_id: str) -> dict[str, Any]:
        try:
            return dict(getattr(orch, fn_name)(run_id).to_dict())
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runs/{run_id}/approve")
    async def approve(run_id: str) -> dict[str, Any]:
        return await run_in_threadpool(_decide, "approve", run_id)

    @app.post("/api/runs/{run_id}/reject")
    async def reject(run_id: str) -> dict[str, Any]:
        return await run_in_threadpool(_decide, "reject", run_id)

    @app.post("/api/runs/{run_id}/escalate")
    async def escalate(run_id: str) -> dict[str, Any]:
        return await run_in_threadpool(_decide, "escalate", run_id)

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            queue = orch.bus.subscribe()
            try:
                for past in orch.bus.history()[-50:]:
                    yield f"data: {json.dumps(past.to_dict())}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(event.to_dict())}\n\n"
                    except TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                orch.bus.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
