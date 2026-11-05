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

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pablo import __version__
from pablo.convos import AUTO, OWNER, Chat, Memory, MemoryStatus
from pablo.orchestrator import Orchestrator, PartialChecksFailed
from pablo.overnight import (
    BriefStore,
    JobQueue,
    OvernightRunner,
    Scheduler,
    build_brief,
    now_local,
)
from pablo.plan import Plan, Status
from pablo.resources import snapshot
from pablo.swap import read_running


class TaskIn(BaseModel):
    task: str
    lane: str | None = None
    task_id: str | None = None


class StatusIn(BaseModel):
    status: Status


class ApproveIn(BaseModel):
    hunks: list[str] | None = None
    force: bool = False


class CompareIn(BaseModel):
    task: str
    lanes: list[str] = ["coder", "deep"]
    task_id: str | None = None


class JobIn(BaseModel):
    task: str
    lane: str | None = None
    task_id: str | None = None


class ConversationIn(BaseModel):
    title: str = ""
    lane: str = AUTO


class RenameIn(BaseModel):
    title: str


class SayIn(BaseModel):
    text: str
    lane: str | None = None
    do: bool | None = None  # True: run it · False: just talk · None: the model decides
    confirm: bool = False  # required for a lane that leaves Pablo (Claude), every send


class MemoryIn(BaseModel):
    text: str
    scope: str = "profile"


def asdict_profile(p: Any) -> dict[str, Any]:
    return dict(p.__dict__)


def profile_of(request: Request) -> str:
    """Who is asking. Everyone is the owner until profiles map Tailscale identities."""
    del request
    return OWNER


def create_app(orch: Orchestrator) -> FastAPI:
    app = FastAPI(title="Pablo harness", version=__version__)
    queue = JobQueue(orch.settings.home)
    runner = OvernightRunner(orch, queue, orch.settings.overnight)
    briefs = BriefStore(orch.settings.home)
    app.state.scheduler = Scheduler(runner, briefs, orch.repo)
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

    @app.get("/api/runs")
    def list_runs(limit: int = 30) -> list[dict[str, Any]]:
        return [r.to_dict() for r in orch.recent(max(1, min(limit, 200)))]

    @app.post("/api/voice")
    async def voice() -> dict[str, Any]:
        # Audio stays on Pablo: M-voice adds a local speech-to-text lane behind this.
        raise HTTPException(501, "Voice lane is not set up yet. Type the task for now.")

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

    @app.get("/api/promotions")
    def promotions() -> dict[str, Any]:
        from pablo.learn import list_promotions, load_pending

        pending = load_pending(orch.settings.home)
        return {
            "pending": None if pending is None else pending.to_dict(),
            "history": [p.to_dict() for p in list_promotions(orch.settings.home)],
        }

    @app.post("/api/promotions/promote")
    def promotions_promote() -> dict[str, Any]:
        from pablo.learn import promote_adapter

        try:
            return promote_adapter(orch.settings.home).to_dict()
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/promotions/reject")
    def promotions_reject() -> dict[str, Any]:
        from pablo.learn import reject_promotion

        try:
            return reject_promotion(orch.settings.home).to_dict()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def _decide(fn_name: str, run_id: str) -> dict[str, Any]:
        try:
            return dict(getattr(orch, fn_name)(run_id).to_dict())
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runs/{run_id}/approve")
    async def approve(run_id: str, body: ApproveIn | None = None) -> dict[str, Any]:
        body = body or ApproveIn()

        def go() -> dict[str, Any]:
            try:
                return orch.approve(run_id, body.hunks, force=body.force).to_dict()
            except KeyError as exc:
                raise HTTPException(404, f"no run {run_id}") from exc
            except PartialChecksFailed as exc:
                detail = {"message": str(exc), "verdict": exc.verdict.to_dict()}
                raise HTTPException(409, detail) from exc
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(409, str(exc)) from exc

        return await run_in_threadpool(go)

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str) -> list[dict[str, Any]]:
        try:
            return orch.events(run_id)
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc

    @app.post("/api/compare")
    async def compare(body: CompareIn) -> list[dict[str, Any]]:
        try:
            runs = await run_in_threadpool(
                orch.compare, body.task, body.lanes, task_id=body.task_id
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return [r.to_dict() for r in runs]

    @app.post("/api/runs/{run_id}/reject")
    async def reject(run_id: str) -> dict[str, Any]:
        return await run_in_threadpool(_decide, "reject", run_id)

    @app.post("/api/runs/{run_id}/escalate")
    async def escalate(run_id: str) -> dict[str, Any]:
        return await run_in_threadpool(_decide, "escalate", run_id)

    @app.get("/api/resources")
    async def resources() -> dict[str, Any]:
        snap = await run_in_threadpool(lambda: snapshot(running=read_running))
        return snap.to_dict()

    # ---- overnight queue and morning brief ------------------------------------
    @app.get("/api/queue")
    def list_queue() -> list[dict[str, Any]]:
        jobs = [j for j in queue.all() if j.status != "cancelled"]
        return [j.to_dict() for j in jobs[-50:]]

    @app.post("/api/queue")
    def add_job(body: JobIn) -> dict[str, Any]:
        if body.lane:
            try:
                lane = orch.settings.lane(body.lane)
            except KeyError as exc:
                raise HTTPException(400, str(exc)) from exc
            if lane.requires_approval:
                raise HTTPException(403, f"lane '{lane.name}' can't be queued; escalate instead")
        return queue.add(body.task, body.lane, body.task_id).to_dict()

    @app.delete("/api/queue/{job_id}")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return queue.cancel(job_id).to_dict()
        except KeyError as exc:
            raise HTTPException(404, f"no job {job_id}") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/queue/run")
    async def run_queue_now() -> list[dict[str, Any]]:
        finished = await run_in_threadpool(runner.run_pending, force=True)
        return [j.to_dict() for j in finished]

    @app.get("/api/brief")
    def get_brief(day: str | None = None) -> dict[str, Any]:
        brief = briefs.get(day) if day else briefs.latest()
        if brief is None:
            raise HTTPException(404, "no brief yet")
        return brief

    @app.post("/api/brief")
    def make_brief() -> dict[str, Any]:
        brief = build_brief(orch, queue, now_local().date(), orch.repo, since=briefs.last_created())
        briefs.save(brief)
        return brief.to_dict()

    chat = Chat(orch)
    store = chat.store

    def _found(fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc

    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        return asdict_profile(store.profile(profile_of(request)))

    @app.get("/api/conversations")
    def list_conversations(request: Request, limit: int = 50) -> list[dict[str, Any]]:
        convs = store.conversations(profile_of(request), max(1, min(limit, 200)))
        return [c.__dict__ for c in convs]

    @app.post("/api/conversations")
    def new_conversation(body: ConversationIn, request: Request) -> dict[str, Any]:
        try:
            return chat.start(profile_of(request), body.title, body.lane).__dict__
        except KeyError as exc:
            raise HTTPException(400, str(exc).strip("'")) from exc

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: str, request: Request) -> dict[str, Any]:
        conv = _found(store.conversation, cid, profile_of(request))
        return {
            "conversation": conv.__dict__,
            "messages": [m.__dict__ for m in store.messages(cid)],
        }

    @app.patch("/api/conversations/{cid}")
    def rename_conversation(cid: str, body: RenameIn, request: Request) -> dict[str, Any]:
        _found(store.rename, cid, profile_of(request), body.title)
        return store.conversation(cid, profile_of(request)).__dict__

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: str, request: Request) -> dict[str, Any]:
        _found(store.delete, cid, profile_of(request))
        return {"deleted": cid}

    @app.post("/api/conversations/{cid}/messages")
    async def say(cid: str, body: SayIn, request: Request) -> dict[str, Any]:
        who = profile_of(request)
        try:
            turn = await run_in_threadpool(
                chat.say, cid, body.text, profile=who, lane=body.lane, do=body.do,
                confirm=body.confirm,
            )  # fmt: skip
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return turn.to_dict()

    @app.get("/api/memories")
    def list_memories(request: Request, status: MemoryStatus | None = None) -> list[dict[str, Any]]:
        return [m.__dict__ for m in store.memories(profile_of(request), status=status)]

    @app.post("/api/memories")
    def add_memory(body: MemoryIn, request: Request) -> dict[str, Any]:
        return store.remember(profile_of(request), body.text, scope=body.scope).__dict__

    @app.post("/api/memories/{mid}/keep")
    def keep_memory(mid: str, request: Request) -> dict[str, Any]:
        mem: Memory = _found(store.decide, mid, profile_of(request), "kept")
        return dict(mem.__dict__)

    @app.post("/api/memories/{mid}/reject")
    def reject_memory(mid: str, request: Request) -> dict[str, Any]:
        mem: Memory = _found(store.decide, mid, profile_of(request), "rejected")
        return dict(mem.__dict__)

    @app.delete("/api/memories/{mid}")
    def forget_memory(mid: str, request: Request) -> dict[str, Any]:
        store.forget(mid, profile_of(request))
        return {"forgot": mid}

    @app.get("/api/search")
    def search(q: str, request: Request) -> dict[str, Any]:
        return store.search(profile_of(request), q)

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
