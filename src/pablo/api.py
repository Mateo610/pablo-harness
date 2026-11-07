"""Harness API: the one surface the web cockpit, TUI, and phone all talk to.

Bind it to 127.0.0.1 or your Tailscale IP only. Never expose it publicly; it can
write to your repo when you approve.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from pydantic import BaseModel

from pablo import __version__
from pablo.access import TOKEN_COOKIE, Access, Caller, Denied
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
from pablo.plans import PlanBusy, Plans
from pablo.projects import Workspace
from pablo.resources import snapshot
from pablo.swap import read_running
from pablo.voice import (
    SentenceCutter,
    Speaker,
    Transcriber,
    VoiceOff,
    engines,
    news_line,
    reachable,
    vocabulary,
)

STALE_AFTER = timedelta(days=3)  # a run left waiting this long folds into Stale on Work


class TaskIn(BaseModel):
    task: str
    lane: str | None = None
    task_id: str | None = None
    project: str = ""


class ProjectIn(BaseModel):
    title: str
    about: str = ""
    mode: Literal["standard", "unfiltered"] = "standard"


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
    project: str = ""


class NewsIn(BaseModel):
    run_id: str
    lane: str = "coder"


class ConversationIn(BaseModel):
    title: str = ""
    lane: str = AUTO
    project: str = ""


class RenameIn(BaseModel):
    title: str


class SayIn(BaseModel):
    text: str
    lane: str | None = None
    do: bool | None = None  # True: run it · False: just talk · None: the model decides
    confirm: bool = False  # required for a lane that leaves Pablo (Claude), every send
    stream: bool = False  # reply as NDJSON: {"type": "delta"} pieces, then "done" or "error"
    think: bool = False  # extended thinking (Base and Hybrid only; slower, more careful)


class ModeIn(BaseModel):
    mode: Literal["interactive", "balanced", "deep"]


class MemoryIn(BaseModel):
    text: str
    scope: str = "profile"


class NoteEditIn(BaseModel):
    text: str


def asdict_profile(p: Any) -> dict[str, Any]:
    return dict(p.__dict__)


def _say_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, PermissionError):
        return 403, str(exc)
    if isinstance(exc, KeyError):
        return 404, str(exc).strip("'")
    if isinstance(exc, ValueError):
        return 400, str(exc)
    return 502, f"the model call failed: {exc}"


def caller_of(request: Request) -> Caller:
    caller: Caller = request.state.caller
    return caller


def profile_of(request: Request) -> str:
    return caller_of(request).profile


def owner_only(request: Request) -> None:
    if not caller_of(request).is_owner:
        raise HTTPException(403, "Only the owner can do that.")


def _token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(TOKEN_COOKIE, "") or request.query_params.get("token", "")


def join_page(title: str, detail: str, go: bool = False) -> str:
    """The small page an invite link lands on. Plain HTML: the cockpit loads right after."""
    from html import escape

    refresh = '<meta http-equiv="refresh" content="2;url=/">' if go else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Pablo</title>{refresh}"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;"
        "padding:0 1.5rem;color-scheme:light dark}h1{font-size:1.5rem}</style></head><body>"
        f"<h1>{escape(title, quote=False)}</h1><p>{escape(detail, quote=False)}</p>"
        + ('<p><a href="/">Open Pablo</a></p>' if go else "")
        + "</body></html>"
    )


def create_app(
    orch: Orchestrator,
    access: Access | None = None,
    voice_engines: tuple[Transcriber, Speaker] | None = None,
    workspace: Workspace | None = None,
) -> FastAPI:
    app = FastAPI(title="Pablo harness", version=__version__)
    ws = workspace or Workspace(orch)
    queue = JobQueue(orch.settings.home)
    runner = OvernightRunner(orch, queue, orch.settings.overnight, workspace=ws)
    briefs = BriefStore(orch.settings.home)
    app.state.scheduler = Scheduler(runner, briefs, orch.repo)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    repo: Path = orch.repo
    gate = access or Access(orch.settings.home)
    app.state.access = gate
    app.state.workspace = ws
    plans = Plans(ws, background=not orch.retry_inline)
    chat = Chat(orch, background=True, workspace=ws, plans=plans)
    store = chat.store

    @app.middleware("http")
    async def identify(request: Request, call_next: Any) -> Response:
        path = request.url.path
        guarded = path.startswith("/api/") and path != "/api/health"
        if not guarded and "token" not in request.query_params:
            response: Response = await call_next(request)
            return response
        client = request.client.host if request.client else None
        server = request.scope.get("server")
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            caller = await run_in_threadpool(
                gate.resolve,
                client,
                server[0] if server else None,
                headers,
                _token(request),
                request.method,
            )
        except Denied as exc:
            if not guarded:
                response = await call_next(request)
                return response
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        request.state.caller = caller
        if caller.profile != OWNER:
            await run_in_threadpool(store.ensure_profile, caller.profile, caller.name, caller.role)
        token = request.query_params.get("token")
        if caller.is_owner and token and not guarded and request.method == "GET":
            # The link `pablo token` prints: sign the browser in, then drop the token from the
            # address bar so it doesn't sit in history or get shared with the page's URL.
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            target = path + (f"?{urlencode(rest)}" if rest else "")
            redirect = RedirectResponse(target, status_code=303)
            redirect.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict")
            return redirect
        response = await call_next(request)
        if caller.is_owner and request.query_params.get("token"):
            # `pablo token` gives a link; the cookie keeps the browser signed in on this box.
            response.set_cookie(TOKEN_COOKIE, request.query_params["token"], httponly=True,
                                samesite="strict")  # fmt: skip
        return response

    @app.get("/join", response_class=HTMLResponse)
    async def join(request: Request, code: str = "") -> HTMLResponse:
        """An invite link: a tailnet peer with a valid code goes on the access list."""
        client = request.client.host if request.client else None
        server = request.scope.get("server")
        headers = {k.lower(): v for k, v in request.headers.items()}
        try:
            caller = await run_in_threadpool(
                gate.join, client, server[0] if server else None, headers, code
            )
        except Denied as exc:
            return HTMLResponse(join_page("Not in yet", str(exc)), status_code=exc.status)
        if caller.profile != OWNER:
            await run_in_threadpool(store.ensure_profile, caller.profile, caller.name, caller.role)
        first = caller.name.split()[0] if caller.name else "there"
        return HTMLResponse(join_page(f"You're in, {first}.", "Opening Pablo…", go=True))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "repo": str(repo)}

    @app.get("/api/lanes")
    def lanes() -> list[dict[str, Any]]:
        return [lane.model_dump() for lane in orch.settings.lanes.values()]

    @app.get("/api/plan")
    def plan(project: str = "") -> dict[str, Any]:
        try:
            found = plans.get(project)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        if found is None:
            raise HTTPException(404, "no plan yet: describe what you want built in Chat")
        return found.model_dump(mode="json")

    @app.post("/api/plan/go")
    async def plan_go(request: Request, project: str = "") -> dict[str, Any]:
        """Build the plan: the next step runs; each one waits for the owner's approval."""
        try:
            started = await run_in_threadpool(plans.go, project, by=profile_of(request))
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        except (ValueError, PlanBusy) as exc:
            raise HTTPException(409, str(exc)) from exc
        return started.model_dump(mode="json")

    @app.post("/api/plan/tasks/{task_id}")
    def set_task_status(task_id: str, body: StatusIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        current = Plan.load(repo)
        try:
            task = current.set_status(task_id, body.status)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        current.save(repo)
        return task.model_dump()

    @app.post("/api/runs")
    async def start_run(body: TaskIn, request: Request) -> dict[str, Any]:
        caller = caller_of(request)
        if body.lane == "claude" and not caller.is_owner:
            raise HTTPException(403, "Only the owner can send work to Claude.")
        try:
            target = ws.orch(body.project)
            run = await run_in_threadpool(
                target.start, body.task, lane=body.lane, task_id=body.task_id, by=caller.profile
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(400, str(exc)) from exc
        return run.to_dict()

    @app.get("/api/runs")
    def list_runs(request: Request, limit: int = 30) -> list[dict[str, Any]]:
        return [linked(r, request) for r in orch.recent(max(1, min(limit, 200)))]

    def voice_conflicts() -> set[str]:
        """Lanes whose model can't share the GPU with the voice server (deep, today)."""
        tts = next((r for r in orch.settings.gpu.residents if r.name == "tts"), None)
        models = set(tts.conflicts) if tts else {"pablo/deep"}
        return {name for name, lane in orch.settings.lanes.items() if lane.model in models}

    def _voice() -> tuple[Transcriber, Speaker]:
        try:
            return voice_engines or engines(orch.settings.voice)
        except VoiceOff as exc:
            raise HTTPException(503, str(exc)) from exc

    async def _hear(request: Request, where: Path | None = None) -> str:
        audio = await request.body()
        if not audio:
            raise HTTPException(400, "no audio in the request")
        if len(audio) > 25 * 1024 * 1024:
            raise HTTPException(413, "that recording is too long")
        stt, _ = _voice()
        mime = request.headers.get("content-type", "audio/wav")
        hint = await run_in_threadpool(vocabulary, where or repo, orch.settings.home)
        try:
            return str(await run_in_threadpool(stt.transcribe, audio, mime, hint))
        except Exception as exc:  # the engine is a separate process; say what broke
            raise HTTPException(502, f"speech-to-text failed: {exc}") from exc

    @app.get("/api/voice/status")
    async def voice_status() -> dict[str, Any]:
        cfg = orch.settings.voice
        resident = orch.gpu.residents.get("tts")
        if voice_engines:
            state: str = resident.state if resident else "running"
            return {"enabled": True, "stt": True, "tts": True, "voices": cfg.voices,
                    "tts_state": state}  # fmt: skip
        stt, tts = await asyncio.gather(
            run_in_threadpool(reachable, cfg.stt_url), run_in_threadpool(reachable, cfg.tts_url)
        )
        # The voice server may be paused for deep: it wakes on the next turn, so that's ready.
        # Trust the health check over this process's memory: another Pablo process (a CLI deep
        # run, say) can stop the unit without this one hearing about it.
        if tts:
            state = "running"
        elif resident is None:
            state = "down"
        elif resident.state == "starting":
            state = "starting"
        else:
            state = "paused"
            if resident.state == "running":
                resident.state = "paused"  # so the next turn wakes it, and says so
        return {"enabled": cfg.enabled, "stt": stt, "tts": tts, "voices": cfg.voices,
                "tts_state": state}  # fmt: skip

    @app.post("/api/voice")
    async def voice(request: Request) -> dict[str, Any]:
        """Phone hold-to-talk: what you said becomes a run (the phone shows its card)."""
        text = await _hear(request)
        if not text:
            raise HTTPException(422, "Didn't catch that. Try again?")
        run = await run_in_threadpool(orch.start_background, text, by=profile_of(request))
        return {"text": text, "run": linked(run, request)}

    @app.post("/api/voice/news")
    async def voice_news(body: NewsIn, request: Request) -> dict[str, Any]:
        """A finished run, said out loud: what's ready, what needs you. Skips a run whose
        Hybrid retry is still going (its retry gets its own news)."""
        try:
            run = orch.get(body.run_id)
        except KeyError as exc:
            raise HTTPException(404, f"no run {body.run_id}") from exc
        if (
            run.state not in ("ready", "needs_you", "failed")
            or run.retry_run
            or (run.group and "trying it now" in run.question and not run.retried_from)
        ):
            return {"skip": True}
        text = news_line(run, can_apply=caller_of(request).is_owner)
        _, tts = _voice()
        lane = body.lane if body.lane in orch.settings.voice.voices else "coder"
        try:
            audio = await run_in_threadpool(
                tts.speak, text, orch.settings.voice.voices.get(lane, "")
            )
        except Exception as exc:
            raise HTTPException(502, f"text-to-speech failed: {exc}") from exc
        mime = "audio/mpeg" if orch.settings.voice.tts_format == "mp3" else "audio/wav"
        import base64

        return {"skip": False, "text": text, "mime": mime, "run": run.id,
                "data": base64.b64encode(audio).decode()}  # fmt: skip

    @app.post("/api/voice/turn", response_model=None)
    async def voice_turn(
        request: Request, lane: str = "coder", conversation: str = "", project: str = ""
    ) -> StreamingResponse:
        """One turn of a voice call: audio in; NDJSON out.

        conversation · heard · delta (text as it's written) · audio (one per sentence,
        base64, in order) · done (the turn) or error.
        """
        who = profile_of(request)
        cfg = orch.settings.lanes.get(lane)
        if cfg is None or cfg.kind != "local" or cfg.requires_approval:
            raise HTTPException(400, "Voice talks to a local model on Pablo.")
        if lane in voice_conflicts():
            # The voice server and this model can't share the GPU, so it could never answer aloud.
            raise HTTPException(400, f"{lane} can't talk: it needs the GPU the voice uses.")
        if orch.gpu.mode == "deep":
            raise HTTPException(
                409, "Pablo is in Deep Work mode: voice is paused so deep keeps the GPU."
            )
        if conversation:
            conv = _found(store.conversation, conversation, who)
        else:
            try:
                conv = chat.start(who, lane=lane, project=project)
            except KeyError as exc:
                raise HTTPException(404, str(exc).strip("'\"")) from exc
        text = await _hear(request, chat.orch_for(conv).repo)
        _, tts = _voice()
        voice_name = orch.settings.voice.voices.get(lane, "")
        mime = "audio/mpeg" if orch.settings.voice.tts_format == "mp3" else "audio/wav"

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def put(item: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def work() -> None:
            import base64
            import queue as q
            import threading

            sentences: q.Queue[str | None] = q.Queue()
            spoke: dict[str, Any] = {"n": 0, "error": ""}

            def speaker() -> None:  # synthesis overlaps generation
                while (line := sentences.get()) is not None:
                    try:
                        audio = tts.speak(line, voice_name)
                    except Exception as exc:
                        spoke["error"] = str(exc)
                        continue
                    put({"type": "audio", "seq": spoke["n"], "mime": mime, "text": line,
                         "data": base64.b64encode(audio).decode()})  # fmt: skip
                    spoke["n"] += 1

            voice_thread = threading.Thread(target=speaker, daemon=True)
            voice_thread.start()
            cutter = SentenceCutter()

            def delta(piece: str) -> None:
                put({"type": "delta", "text": piece})
                for line in cutter.feed(piece):
                    sentences.put(line)

            try:
                orch.gpu.ensure_resident(
                    "tts", on_wake=lambda: put({"type": "status", "text": "waking the voice…"})
                )
            except Exception as exc:
                sentences.put(None)
                put({"type": "error", "status": 503, "detail": f"the voice didn't wake: {exc}"})
                return
            try:
                turn = chat.say(conv.id, text, profile=who, lane=lane, on_delta=delta, spoken=True)
                if not turn.reply.run_id and turn.kept:  # "remember: ..." had no stream
                    sentences.put(turn.reply.content)
                for line in cutter.flush():
                    sentences.put(line)
                sentences.put(None)
                voice_thread.join()
                put({"type": "done", "turn": turn.to_dict(), "tts_error": spoke["error"]})
            except Exception as exc:
                sentences.put(None)
                status, detail = _say_error(exc)
                put({"type": "error", "status": status, "detail": detail})

        async def lines() -> AsyncIterator[str]:
            yield json.dumps({"type": "conversation", "id": conv.id}) + "\n"
            yield json.dumps({"type": "heard", "text": text}) + "\n"
            if not text:
                yield json.dumps({"type": "error", "status": 422,
                                  "detail": "Didn't catch that. Try again?"}) + "\n"  # fmt: skip
                return
            loop.run_in_executor(None, work)
            while True:
                item = await queue.get()
                yield json.dumps(item) + "\n"
                if item["type"] in ("done", "error"):
                    break

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> dict[str, Any]:
        try:
            return linked(orch.get(run_id), request)
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc

    def stale_reasons(waiting: list[Any]) -> dict[str, str]:
        """Why a waiting run is no longer worth deciding: replaced, superseded, or old."""
        recent = orch.recent(200)
        by_id = {r.id: r for r in recent}
        applied_groups = {r.group for r in recent if r.group and r.state == "approved"}
        now = datetime.now(UTC)
        out: dict[str, str] = {}
        for r in waiting:
            if r.retry_run and r.retry_run in by_id:
                out[r.id] = f"replaced by its retry {r.retry_run}"
            elif r.group and r.group in applied_groups:
                out[r.id] = "another version of this change was applied"
            else:
                try:
                    age = now - datetime.fromisoformat(r.created_at)
                except ValueError:
                    continue
                if age > STALE_AFTER:
                    out[r.id] = f"waiting {age.days} days"
        return out

    @app.get("/api/inbox")
    def inbox(request: Request) -> list[dict[str, Any]]:
        """Runs waiting for a decision, needs-you first. Each says if it has gone stale."""
        items = sorted(orch.inbox(), key=lambda r: (r.state != "needs_you", r.created_at))
        stale = stale_reasons(items)
        return [
            {**linked(r, request), "stale": r.id in stale, "stale_reason": stale.get(r.id, "")}
            for r in items
        ]

    @app.post("/api/inbox/discard-stale")
    async def discard_stale(request: Request) -> dict[str, Any]:
        """Reject every stale waiting run at once. Nothing in any repo changes."""
        owner_only(request)

        def go() -> dict[str, Any]:
            stale = stale_reasons(orch.inbox())
            done = []
            for run_id in stale:
                try:
                    _decide("reject", run_id)
                    done.append(run_id)
                except HTTPException:
                    continue  # decided meanwhile, or its project is gone
            return {"discarded": done}

        return await run_in_threadpool(go)

    @app.get("/api/promotions")
    def promotions() -> dict[str, Any]:
        from pablo.learn import list_promotions, load_pending

        pending = load_pending(orch.settings.home)
        return {
            "pending": None if pending is None else pending.to_dict(),
            "history": [p.to_dict() for p in list_promotions(orch.settings.home)],
        }

    @app.post("/api/promotions/promote")
    def promotions_promote(request: Request) -> dict[str, Any]:
        owner_only(request)
        from pablo.learn import promote_adapter

        try:
            return promote_adapter(orch.settings.home).to_dict()
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/promotions/reject")
    def promotions_reject(request: Request) -> dict[str, Any]:
        owner_only(request)
        from pablo.learn import reject_promotion

        try:
            return reject_promotion(orch.settings.home).to_dict()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def _decide(fn_name: str, run_id: str) -> dict[str, Any]:
        try:
            done = getattr(ws.for_run(run_id), fn_name)(run_id)
            if fn_name == "reject":
                plans.after(done)  # a rejected plan step pauses its plan
            return dict(done.to_dict())
        except KeyError as exc:
            raise HTTPException(404, f"no run {run_id}") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/runs/{run_id}/approve")
    async def approve(
        run_id: str, request: Request, body: ApproveIn | None = None
    ) -> dict[str, Any]:
        owner_only(request)
        body = body or ApproveIn()

        def go() -> dict[str, Any]:
            try:
                done = ws.for_run(run_id).approve(run_id, body.hunks, force=body.force)
                plans.after(done)  # an approved plan step starts the next one
                return done.to_dict()
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
    async def compare(body: CompareIn, request: Request) -> list[dict[str, Any]]:
        # One task on several lanes holds the GPU for each in turn (deep for minutes).
        owner_only(request)
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
    async def reject(run_id: str, request: Request) -> dict[str, Any]:
        caller = caller_of(request)
        if not caller.is_owner:
            try:
                mine = orch.get(run_id).by == caller.profile
            except KeyError as exc:
                raise HTTPException(404, f"no run {run_id}") from exc
            if not mine:
                raise HTTPException(403, "Only the owner can reject someone else's run.")
        return await run_in_threadpool(_decide, "reject", run_id)

    @app.post("/api/runs/{run_id}/escalate")
    async def escalate(run_id: str, request: Request) -> dict[str, Any]:
        owner_only(request)
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
    def add_job(body: JobIn, request: Request) -> dict[str, Any]:
        try:
            target = ws.orch(body.project)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc
        if body.lane:
            try:
                lane = target.settings.lane(body.lane)
            except KeyError as exc:
                raise HTTPException(400, str(exc)) from exc
            if lane.requires_approval:
                raise HTTPException(403, f"lane '{lane.name}' can't be queued; escalate instead")
        project = "" if target is orch else target.repo.name
        return queue.add(
            body.task, body.lane, body.task_id, project=project, by=profile_of(request)
        ).to_dict()

    @app.delete("/api/queue/{job_id}")
    def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
        owner_only(request)
        try:
            return queue.cancel(job_id).to_dict()
        except KeyError as exc:
            raise HTTPException(404, f"no job {job_id}") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/queue/run")
    async def run_queue_now(request: Request) -> list[dict[str, Any]]:
        owner_only(request)
        finished = await run_in_threadpool(runner.run_pending, force=True)
        return [j.to_dict() for j in finished]

    @app.get("/api/brief")
    def get_brief(day: str | None = None) -> dict[str, Any]:
        brief = briefs.get(day) if day else briefs.latest()
        if brief is None:
            raise HTTPException(404, "no brief yet")
        return brief

    @app.post("/api/brief")
    def make_brief(request: Request) -> dict[str, Any]:
        owner_only(request)
        brief = build_brief(orch, queue, now_local().date(), orch.repo, since=briefs.last_created(),
                            workspace=ws)  # fmt: skip
        briefs.save(brief)
        return brief.to_dict()

    def linked(run: Any, request: Request) -> dict[str, Any]:
        """The run, plus the conversation that started it if that conversation is yours."""
        data: dict[str, Any] = run.to_dict()
        hit = store.conversation_for_run(run.id, profile_of(request))
        data["conversation_id"], data["conversation_lane"], data["conversation_title"] = (
            hit if hit else (None, None, None)
        )
        return data

    def _found(fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'")) from exc

    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        caller = caller_of(request)
        return {**asdict_profile(store.profile(caller.profile)), **caller.to_dict(),
                "can_approve": caller.is_owner}  # fmt: skip

    @app.get("/api/conversations")
    def list_conversations(request: Request, limit: int = 50) -> list[dict[str, Any]]:
        convs = store.conversations(profile_of(request), max(1, min(limit, 200)))
        return [c.__dict__ for c in convs]

    @app.post("/api/conversations")
    def new_conversation(body: ConversationIn, request: Request) -> dict[str, Any]:
        if body.lane == "claude":
            owner_only(request)
        try:
            return chat.start(profile_of(request), body.title, body.lane, body.project).__dict__
        except KeyError as exc:
            raise HTTPException(400, str(exc).strip("'")) from exc

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: str, request: Request) -> dict[str, Any]:
        conv = _found(store.conversation, cid, profile_of(request))
        return {
            "conversation": conv.__dict__,
            "messages": [m.__dict__ for m in store.messages(cid)],
            "unfiltered": chat.unfiltered(conv.project),
        }

    @app.patch("/api/conversations/{cid}")
    def rename_conversation(cid: str, body: RenameIn, request: Request) -> dict[str, Any]:
        _found(store.rename, cid, profile_of(request), body.title)
        return store.conversation(cid, profile_of(request)).__dict__

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: str, request: Request) -> dict[str, Any]:
        _found(store.delete, cid, profile_of(request))
        return {"deleted": cid}

    @app.post("/api/conversations/{cid}/messages", response_model=None)
    async def say(cid: str, body: SayIn, request: Request) -> dict[str, Any] | StreamingResponse:
        who = profile_of(request)
        lane = body.lane or _found(store.conversation, cid, who).lane
        if lane == "claude":
            owner_only(request)
        kw: dict[str, Any] = {"profile": who, "lane": body.lane, "do": body.do,
                              "confirm": body.confirm, "think": body.think}  # fmt: skip
        if not body.stream:
            try:
                turn = await run_in_threadpool(chat.say, cid, body.text, **kw)
            except (PermissionError, KeyError, ValueError) as exc:
                status, detail = _say_error(exc)
                raise HTTPException(status, detail) from exc
            return turn.to_dict()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def put(item: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def work() -> None:
            try:
                delta = lambda t: put({"type": "delta", "text": t})  # noqa: E731
                turn = chat.say(cid, body.text, on_delta=delta, **kw)
                put({"type": "done", "turn": turn.to_dict()})
            except Exception as exc:  # the stream must always end with done or error
                status, detail = _say_error(exc)
                put({"type": "error", "status": status, "detail": detail})

        loop.run_in_executor(None, work)

        async def lines() -> AsyncIterator[str]:
            while True:
                item = await queue.get()
                yield json.dumps(item) + "\n"
                if item["type"] != "delta":
                    break

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.get("/api/memories")
    def list_memories(request: Request, status: MemoryStatus | None = None) -> list[dict[str, Any]]:
        return [m.__dict__ for m in store.memories(profile_of(request), status=status)]

    @app.post("/api/memories")
    def add_memory(body: MemoryIn, request: Request) -> dict[str, Any]:
        return store.keep(profile_of(request), body.text, scope=body.scope).__dict__

    @app.patch("/api/memories/{mid}")
    def edit_memory(mid: str, body: NoteEditIn, request: Request) -> dict[str, Any]:
        try:
            mem: Memory = _found(store.edit, mid, profile_of(request), body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return dict(mem.__dict__)

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

    @app.get("/api/gpu")
    def gpu_state() -> dict[str, Any]:
        """Mode, who has the GPU, and who's waiting, most urgent first."""
        return orch.gpu.snapshot()

    @app.post("/api/gpu/mode")
    def gpu_mode(body: ModeIn, request: Request) -> dict[str, Any]:
        owner_only(request)
        orch.gpu.set_mode(body.mode)
        return orch.gpu.snapshot()

    @app.get("/api/repo")
    def repo_info(project: str = "") -> dict[str, Any]:
        from pablo.stats import repo_status

        try:
            return repo_status(ws.orch(project).repo)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip("'\"")) from exc

    # ---- projects ---------------------------------------------------------------------------
    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [p.to_dict() for p in ws.projects()]

    @app.post("/api/projects")
    def create_project(body: ProjectIn, request: Request) -> dict[str, Any]:
        owner_only(request)  # a new folder on Pablo's disk
        try:
            return ws.create(body.title, body.about, body.mode).to_dict()
        except FileExistsError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/stats")
    def stats(days: int = 1) -> dict[str, Any]:
        from datetime import UTC, datetime, timedelta

        from pablo.stats import lane_stats

        days = max(1, min(days, 90))
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        return lane_stats(orch, days, store.usage_since(since))

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
