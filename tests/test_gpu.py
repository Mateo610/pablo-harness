"""One GPU, many people: urgency order, sharing the loaded model, quiet-time background work."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pablo.api import create_app
from pablo.config import Overnight, Settings
from pablo.gateway import FakeBackend
from pablo.gpu import GpuScheduler, Priority, ScheduledBackend, job
from pablo.orchestrator import Orchestrator

DAY = datetime(2026, 11, 5, 15, 0)
NIGHT = datetime(2026, 11, 5, 2, 0)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def sched(tmp_path: Path, idle_s: float = 0, at: datetime = DAY) -> tuple[GpuScheduler, Clock]:
    clock = Clock()
    return GpuScheduler(tmp_path, idle_s=idle_s, overnight=Overnight(), clock=clock,
                        local_now=lambda: at), clock  # fmt: skip


class Holder:
    """A thread that takes a lease and holds it until released."""

    def __init__(self, s: GpuScheduler, model: str, prio: Priority, who: str, log: list[str]):
        self.got, self.done = threading.Event(), threading.Event()
        self.thread = threading.Thread(
            target=self._run, args=(s, model, prio, who, log), daemon=True
        )
        self.thread.start()

    def _run(self, s: GpuScheduler, model: str, prio: Priority, who: str, log: list[str]) -> None:
        with job(prio, who), s.lease(model):
            log.append(f"{who}:{model}")
            self.got.set()
            self.done.wait(10)

    def release(self) -> None:
        self.done.set()
        self.thread.join(5)


def until(cond: Callable[[], bool], timeout: float = 5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def waiting(s: GpuScheduler) -> int:
    return len(s.snapshot()["waiting"])


def test_calls_for_the_loaded_model_share_it(tmp_path: Path) -> None:
    s, _ = sched(tmp_path)
    log: list[str] = []
    a = Holder(s, "coder", Priority.RUN, "a", log)
    b = Holder(s, "coder", Priority.RUN, "b", log)
    assert a.got.wait(2) and b.got.wait(2)
    assert len(s.snapshot()["holders"]) == 2
    a.release(), b.release()


def test_a_different_model_waits_and_the_most_urgent_goes_next(tmp_path: Path) -> None:
    s, _ = sched(tmp_path)
    log: list[str] = []
    first = Holder(s, "coder", Priority.RUN, "matt", log)
    assert first.got.wait(2)
    deep = Holder(s, "deep", Priority.RUN, "sam", log)
    assert until(lambda: waiting(s) == 1)
    voice = Holder(s, "advisor", Priority.VOICE, "matt", log)
    assert until(lambda: waiting(s) == 2)
    assert [w["priority"] for w in s.snapshot()["waiting"]] == ["voice", "run"]
    # Someone more urgent waits for another model: a new coder call can't slip in ahead.
    late = Holder(s, "coder", Priority.RUN, "ana", log)
    assert until(lambda: waiting(s) == 3) and not late.got.wait(0.2)
    first.release()
    assert voice.got.wait(2) and not deep.got.is_set()
    voice.release()
    assert until(lambda: len(log) == 3)
    for h in (deep, late):
        h.release()
    assert log[:2] == ["matt:coder", "matt:advisor"]


def test_the_person_served_longest_ago_goes_first(tmp_path: Path) -> None:
    s, _ = sched(tmp_path)
    log: list[str] = []
    hog = Holder(s, "coder", Priority.RUN, "matt", log)
    assert hog.got.wait(2)
    again = Holder(s, "deep", Priority.RUN, "matt", log)
    sam = Holder(s, "deep", Priority.RUN, "sam", log)
    assert until(lambda: waiting(s) == 2)
    hog.release()
    assert sam.got.wait(2) and again.got.wait(2)  # same model: they share once it's loaded
    assert log[1] == "sam:deep"
    sam.release(), again.release()


def test_background_work_waits_for_quiet_in_balanced_mode(tmp_path: Path) -> None:
    s, clock = sched(tmp_path, idle_s=120)
    log: list[str] = []
    live = Holder(s, "coder", Priority.CHAT, "matt", log)
    assert live.got.wait(2)
    live.release()
    retry = Holder(s, "deep", Priority.RETRY, "matt", log)
    assert not retry.got.wait(0.3)  # Matt was just chatting
    assert s.snapshot()["background_ok"] is False
    clock.t += 121
    s.poke()
    assert retry.got.wait(2)
    retry.release()


def test_interactive_mode_keeps_night_work_for_the_night(tmp_path: Path) -> None:
    s, _ = sched(tmp_path, at=DAY)
    s.set_mode("interactive")
    log: list[str] = []
    night = Holder(s, "deep", Priority.NIGHT, "", log)
    assert not night.got.wait(0.3)
    s.set_mode("deep")  # deep work: background runs any time
    assert night.got.wait(2)
    night.release()
    s2, _ = sched(tmp_path / "n", at=NIGHT)
    s2.set_mode("interactive")
    assert s2.background_ok(Priority.NIGHT) and not s2.background_ok(Priority.RETRY)


def test_the_mode_survives_a_restart(tmp_path: Path) -> None:
    s, _ = sched(tmp_path)
    s.set_mode("deep")
    assert sched(tmp_path)[0].mode == "deep"
    with pytest.raises(ValueError):
        s.set_mode("turbo")  # type: ignore[arg-type]


def test_the_router_on_the_cpu_never_waits_for_the_gpu(tmp_path: Path) -> None:
    s, _ = sched(tmp_path)
    log: list[str] = []
    deep = Holder(s, "pablo/deep", Priority.RUN, "x", log)
    assert deep.got.wait(2)
    backend = ScheduledBackend(FakeBackend(), s, ["pablo/router"])
    done = threading.Event()
    threading.Thread(
        target=lambda: (backend.complete("pablo/router", []), done.set()), daemon=True
    ).start()
    assert done.wait(2)
    blocked = threading.Event()
    threading.Thread(
        target=lambda: (backend.complete("pablo/coder", []), blocked.set()), daemon=True
    ).start()
    assert not blocked.wait(0.3)
    deep.release()
    assert blocked.wait(2)


def test_interactive_mode_moves_a_retry_to_tonight(settings: Settings, repo: Path) -> None:
    settings.lanes["coder"].retry_when = "now"
    backend = FakeBackend(scripts={"pablo/coder": lambda m, x: "I'm not sure what to change."})
    o = Orchestrator(
        settings, backend, repo=repo, checks={"tests": [sys.executable, "-m", "pytest", "-q"]}
    )
    o.retry_inline = True
    o.gpu.set_mode("interactive")
    run = o.start("add sub(a, b)", lane="coder")
    assert run.state == "needs_you" and run.retry_job and run.retry_run is None
    assert "tonight" in run.question


def test_the_api_shows_the_queue_and_only_the_owner_switches(
    settings: Settings, repo: Path
) -> None:
    o = Orchestrator(settings, FakeBackend(), repo=repo, checks={})
    client = TestClient(create_app(o), client=("127.0.0.1", 5000))
    assert client.get("/api/gpu").json()["mode"] == "balanced"
    assert client.post("/api/gpu/mode", json={"mode": "deep"}).json()["mode"] == "deep"
    assert client.post("/api/gpu/mode", json={"mode": "warp"}).status_code == 422
    paused = client.post("/api/voice/turn?lane=coder", content=b"W")
    assert paused.status_code == 409 and "Deep Work" in paused.json()["detail"]
    assert json.loads((settings.home / "gpu-mode.json").read_text())["mode"] == "deep"


# residents: the voice server lives on the GPU outside llama-swap -----------------------------


class Box:
    """A fake systemd + health URL + llama-swap unload, recording what Pablo asked for."""

    def __init__(self, up: bool = True, warm_after: int = 0) -> None:
        self.is_up, self.warm_after, self.polls = up, warm_after, 0
        self.log: list[str] = []

    def run(self, command: str) -> None:
        self.log.append(command.split()[2])  # start / stop
        if "stop" in command:
            self.is_up = False
        else:
            self.polls = 0

    def up(self, url: str) -> bool:
        if not self.is_up and self.log and self.log[-1] == "start":
            self.polls += 1
            self.is_up = self.polls > self.warm_after
        return self.is_up

    def unload(self, url: str) -> None:
        self.log.append("unload")


def with_tts(tmp_path: Path, box: Box, warmup_s: float = 90) -> GpuScheduler:
    from pablo.config import ResidentConfig

    tts = ResidentConfig(name="tts", start="systemctl --user start pablo-tts.service",
                         stop="systemctl --user stop pablo-tts.service",
                         health="http://127.0.0.1:8880", conflicts=["pablo/deep"],
                         warmup_s=warmup_s)  # fmt: skip
    return GpuScheduler(tmp_path, idle_s=0, residents_cfg=[tts], unload_url="http://x/unload",
                        run=box.run, up=box.up, unload=box.unload,
                        sleep=lambda s: None)  # fmt: skip


def test_deep_pauses_the_voice_server_before_it_loads(tmp_path: Path) -> None:
    box = Box()
    s = with_tts(tmp_path, box)
    with s.lease("pablo/coder"):
        pass
    assert box.log == []  # the coder fits beside the voice
    with s.lease("pablo/deep"):
        assert box.log == ["stop"] and not box.is_up
    with s.lease("pablo/deep"):
        pass
    assert box.log == ["stop"]  # already paused: no second stop
    assert s.snapshot()["residents"] == [
        {"name": "tts", "state": "paused", "paused_for": "pablo/deep"}
    ]


def test_a_voice_turn_wakes_it_after_unloading_an_idle_deep(tmp_path: Path) -> None:
    box = Box(warm_after=3)
    s = with_tts(tmp_path, box)
    with s.lease("pablo/deep"):
        pass
    woke: list[bool] = []
    assert s.ensure_resident("tts", on_wake=lambda: woke.append(True)) is True
    assert box.log == ["stop", "unload", "start"] and woke == [True]
    assert s.snapshot()["resident"] is None  # deep is gone from VRAM
    assert s.residents["tts"].state == "running"
    assert s.ensure_resident("tts") is False  # up already: nothing to do
    assert box.log == ["stop", "unload", "start"]


def test_waking_waits_for_deep_to_finish_its_work(tmp_path: Path) -> None:
    box = Box()
    s = with_tts(tmp_path, box)
    log: list[str] = []
    deep = Holder(s, "pablo/deep", Priority.RUN, "matt", log)
    assert deep.got.wait(2)
    done = threading.Event()
    t = threading.Thread(target=lambda: (s.ensure_resident("tts"), done.set()), daemon=True)
    t.start()
    time.sleep(0.2)
    assert not done.is_set() and "start" not in box.log  # mid-generation: not evicted
    deep.done.set()
    assert done.wait(3)
    assert box.log == ["stop", "unload", "start"]


def test_a_voice_server_that_never_comes_up_is_an_error(tmp_path: Path) -> None:
    from pablo.gpu import ResidentError

    box = Box(up=False, warm_after=10_000)
    s = with_tts(tmp_path, box, warmup_s=3)
    with pytest.raises(ResidentError, match="didn't come up"):
        s.ensure_resident("tts")


def test_every_process_sees_the_mode_the_other_set(tmp_path: Path) -> None:
    serve, _ = sched(tmp_path)
    cli, _ = sched(tmp_path)
    assert serve.mode == "balanced"
    with cli.mode_for("deep"):
        assert serve.mode == "deep"  # an eval in another process holds Deep Work
    assert serve.mode == "balanced" and cli.mode == "balanced"


# other Pablo processes: one lease table for the machine ----------------------------------------


def test_another_process_holding_a_different_model_makes_us_wait(tmp_path: Path) -> None:
    serve, _ = sched(tmp_path)
    cli, _ = sched(tmp_path)  # a second scheduler on the same table: another process
    log: list[str] = []
    deep = Holder(cli, "pablo/deep", Priority.NIGHT, "eval", log)
    assert deep.got.wait(2)
    assert serve.snapshot()["elsewhere"] == [
        {"model": "pablo/deep", "state": "holding", "who": "eval", "priority": "night"}
    ]
    also_deep = Holder(serve, "pablo/deep", Priority.CHAT, "matt", log)
    assert also_deep.got.wait(2)  # the same model shares, like it does in one process
    also_deep.done.set()
    coder = Holder(serve, "pablo/coder", Priority.CHAT, "matt", log)
    assert not coder.got.wait(0.6)  # deep is on the GPU in the other process
    deep.done.set()
    assert coder.got.wait(3)
    coder.done.set()


def test_a_more_urgent_caller_elsewhere_goes_first(tmp_path: Path) -> None:
    serve, _ = sched(tmp_path)
    cli, _ = sched(tmp_path)
    serve.table.publish([{"model": "pablo/coder", "priority": int(Priority.VOICE),
                          "who": "matt", "since": 0.0, "state": "waiting"}])  # fmt: skip
    log: list[str] = []
    night = Holder(cli, "pablo/deep", Priority.NIGHT, "practice", log)
    assert not night.got.wait(0.6)  # a voice turn is waiting for the coder
    serve.table.publish([])  # the voice turn got its answer
    assert night.got.wait(3)
    night.done.set()


def test_leases_from_a_process_that_died_dont_count(tmp_path: Path) -> None:
    s, _ = sched(tmp_path)
    (tmp_path / "gpu-leases.json").write_text(json.dumps({"entries": [
        {"owner": "gone", "pid": 4_000_000, "beat": time.time(), "model": "pablo/deep",
         "priority": 2, "who": "", "since": 0.0, "state": "holding"}]}))  # fmt: skip
    with s.lease("pablo/coder"):
        pass
    assert s.snapshot()["elsewhere"] == []


def test_the_voice_waits_for_deep_in_another_process_then_unloads_it(tmp_path: Path) -> None:
    box = Box(up=False)  # paused for deep
    voice = with_tts(tmp_path, box)
    cli, _ = sched(tmp_path)
    log: list[str] = []
    deep = Holder(cli, "pablo/deep", Priority.RUN, "eval", log)
    assert deep.got.wait(2)
    done = threading.Event()
    threading.Thread(target=lambda: (voice.ensure_resident("tts"), done.set()), daemon=True).start()
    assert not done.wait(0.6)
    deep.done.set()
    assert done.wait(3)
    assert box.log == ["unload", "start"]  # deep was last on the GPU: unloaded before the voice


def test_an_eval_queues_on_the_real_homes_table(settings: Settings) -> None:
    from pablo.evals import run_suite
    from pablo.sandbox import LocalSandbox

    seen: list[bool] = []

    def coder(model: str, msgs: object) -> str:
        seen.append((settings.home / "gpu-leases.json").is_file())
        return "I'm not sure."

    backend = FakeBackend(scripts={"pablo/coder": coder})
    run_suite(settings, backend, only={"ledger-by-payee-sums"}, python=sys.executable,
              sandbox=LocalSandbox())  # fmt: skip
    assert seen and all(seen)  # the eval's leases are in the real home, not its throwaway one
