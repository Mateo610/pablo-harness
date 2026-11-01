#!/usr/bin/env python3
"""20 coder runs on the M1 sandbox, with the router checked between GPU swaps.

Cycles the three M1 tasks. Each task is staged to the file state it had then
(mul missing, mul returning a sum, sub missing) because the sandbox already
contains the finished functions, and a coder that sees no bug never calls the
judge. Nothing is approved. The original files are written back at the end.
Prints swap times, how many runs repaired their own edits, and any failure.
Exits non-zero on an OOM, an engine crash, or a router that left /running.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from pablo.config import load_settings
from pablo.demo import load_checks
from pablo.gateway import LiteLLMBackend, Message
from pablo.orchestrator import Orchestrator
from pablo.swap import SwapView, parse_running, read_running, swap_times

SANDBOX = Path.home() / "pablo-sandbox" / "hello"
HELLO = SANDBOX / "hello.py"
TEST = SANDBOX / "test_hello.py"
TASKS = (
    "add mul(a, b) with a test",
    "fix mul (returned a sum)",
    "add sub(a, b) with a test",
)
RUNS = 20
ROUTER_PROBES = frozenset({5, 10, 15, 20})

_GREET = '''\
"""Scratch module for Pablo fake-mode runs."""


def greet(name: str) -> str:
    return f"hello {name}"
'''

_MUL_SUM = '''\


def mul(a: int, b: int) -> int:
    return a + b
'''

_MUL_PRODUCT = '''\


def mul(a: int, b: int) -> int:
    return a * b
'''

_TEST_GREET = '''\
from hello import greet


def test_greet() -> None:
    assert greet("pablo") == "hello pablo"
'''

_TEST_MUL = '''\


def test_mul() -> None:
    assert mul(2, 3) == 6
'''


def _stage(task_index: int) -> None:
    """Put the sandbox in the state that M1 task expects. Not applied as a run."""
    which = task_index % 3
    if which == 0:
        HELLO.write_text(_GREET)
        TEST.write_text(_TEST_GREET)
    elif which == 1:
        HELLO.write_text(_GREET + _MUL_SUM)
        TEST.write_text(_TEST_GREET.replace("import greet", "import greet, mul") + _TEST_MUL)
    else:
        HELLO.write_text(_GREET + _MUL_PRODUCT)
        TEST.write_text(_TEST_GREET.replace("import greet", "import greet, mul") + _TEST_MUL)


def main() -> None:
    if not SANDBOX.is_dir():
        sys.exit(f"missing sandbox {SANDBOX}")
    settings = load_settings()
    backend = LiteLLMBackend(settings.gateway.base_url, settings.gateway.api_key, timeout_s=900)
    orch = Orchestrator(
        settings,
        backend,
        repo=SANDBOX,
        checks=load_checks(SANDBOX),
        swap=SwapView(),
    )
    samples: list[tuple[float, dict[str, str]]] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            try:
                samples.append((time.perf_counter(), parse_running(read_running())))
            except OSError:
                samples.append((time.perf_counter(), {}))
            stop.wait(0.25)

    watcher = threading.Thread(target=poll, daemon=True)
    watcher.start()
    original_hello = HELLO.read_bytes()
    original_test = TEST.read_bytes()
    repaired = 0
    try:
        for index in range(1, RUNS + 1):
            task = TASKS[(index - 1) % len(TASKS)]
            _stage(index - 1)
            print(f"--- run {index}/{RUNS}: {task}", flush=True)
            started = time.perf_counter()
            try:
                run = orch.start(task, lane="coder")
            except Exception as exc:
                text = str(exc).lower()
                kind = "oom" if "out of memory" in text or "oom" in text else "crash"
                sys.exit(f"run {index} {kind}: {exc}")
            repaired += int(run.repaired)
            states = parse_running(read_running())
            print(
                f"    {run.id} {run.state} repaired={run.repaired} "
                f"certainty={run.certainty} {time.perf_counter() - started:.0f}s "
                f"loaded={sorted(states)}",
                flush=True,
            )
            if states.get("router") != "ready":
                sys.exit(f"run {index}: router not resident: {states}")
            gpu = {name for name in ("coder", "judge") if states.get(name) == "ready"}
            if gpu == {"coder", "judge"}:
                sys.exit(f"run {index}: coder and judge both resident")
            if index in ROUTER_PROBES:
                _probe_router(backend)
    finally:
        stop.set()
        watcher.join(timeout=2)
        HELLO.write_bytes(original_hello)
        TEST.write_bytes(original_test)

    to_judge, to_coder = swap_times(samples)
    print(f"repaired {repaired}/{RUNS}")
    print(f"coder→judge n={len(to_judge)} {_stats(to_judge)}")
    print(f"judge→coder n={len(to_coder)} {_stats(to_coder)}")


def _probe_router(backend: LiteLLMBackend) -> None:
    messages: list[Message] = [{"role": "user", "content": "Reply with the single word ok."}]
    reply = backend.complete("pablo/router", messages, temperature=0.0, max_tokens=16)
    states = parse_running(read_running())
    print(f"    router probe: {reply.text.strip()!r} state={states.get('router')}", flush=True)
    if states.get("router") != "ready":
        sys.exit(f"router evicted after probe: {states}")


def _stats(spans: list[float]) -> str:
    if not spans:
        return "none"
    ordered = sorted(spans)
    return (
        f"min={ordered[0]:.1f}s median={ordered[len(ordered) // 2]:.1f}s max={ordered[-1]:.1f}s"
    )


if __name__ == "__main__":
    main()
