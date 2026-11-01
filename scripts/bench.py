#!/usr/bin/env python3
"""Bench one local engine on five fixed prompts.

Measures load time, time to first token, decode tokens/s, peak VRAM, and peak RSS.
The server command must bind to 127.0.0.1 (or a Tailscale address), never 0.0.0.0.

  python scripts/bench.py --label "llama.cpp example" --port 8013 \\
      --append docs/benchmarks.md -- \\
      llama-server -m model.gguf --host 127.0.0.1 --port 8013
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DECODE_TOKENS = 128
LOAD_TIMEOUT_S = 900.0


@dataclass(frozen=True)
class Prompt:
    name: str
    kind: str
    text: str


@dataclass
class PromptResult:
    prompt: Prompt
    prompt_tokens: int
    completion_tokens: int
    ttft_s: float
    decode_tok_s: float


def prompts() -> list[Prompt]:
    unit = "alpha bravo charlie delta echo foxtrot golf hotel "
    long_text = (
        "Long-context bench. Read all of this, then count from 1 to 40 with spaces between the numbers.\n"
        + (unit * 1100)
    )
    return [
        Prompt(
            "judge-good-diff",
            "judge",
            "Task: Add slugify that lowercases and hyphenates words.\n"
            "Evidence: checks passed.\n"
            "Diff:\n"
            "@@ function slugify(text):\n"
            "+    words = re.findall(r'[a-z0-9]+', text.lower())\n"
            "+    return '-'.join(words)\n"
            'Reply with JSON only: {"certainty": 0.0, "summary": "one sentence", "question": ""}',
        ),
        Prompt(
            "judge-deleted-test",
            "judge",
            "Task: Change truncate to a single unicode ellipsis.\n"
            "Evidence: checks passed.\n"
            "Diff:\n"
            "-            return text[:limit] + \"...\"\n"
            "+            return text[:limit] + \"…\"\n"
            "-        def test_truncate_long_text():\n"
            "-            assert truncate(\"abcdefghij\", 4) == \"abcd...\"\n"
            "The test that locked the old behavior was deleted and checks still pass.\n"
            'Reply with JSON only: {"certainty": 0.0, "summary": "one sentence", "question": ""}',
        ),
        Prompt(
            "code-mul",
            "code",
            "Write a Python function mul(a: int, b: int) -> int that returns the product, "
            "plus one pytest. Code only.",
        ),
        Prompt(
            "code-word-count",
            "code",
            "Write a Python function word_count(text: str) -> int that counts words separated "
            "by any whitespace, including repeated spaces. Code only.",
        ),
        Prompt("long-context-8k", "long", long_text),
    ]


def reject_public_bind(argv: list[str]) -> None:
    """Refuse a server command that would listen on all interfaces."""
    for arg in argv:
        if arg == "0.0.0.0" or arg.endswith("0.0.0.0"):
            raise SystemExit("refusing to bind 0.0.0.0; use 127.0.0.1 or the Tailscale IP")


def render_table(
    label: str,
    load_s: float,
    peak_vram_mib: int,
    peak_rss_mib: int,
    rows: list[PromptResult],
) -> str:
    lines = [
        f"### Bench · {label}",
        "",
        f"Load {load_s:.1f} s. Peaks over the whole run: "
        f"VRAM {peak_vram_mib} MiB, RSS {peak_rss_mib} MiB.",
        "",
        "| prompt | kind | prompt tokens | ttft s | decode tok/s |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.prompt.name} | {row.prompt.kind} | {row.prompt_tokens} | "
            f"{row.ttft_s:.2f} | {row.decode_tok_s:.1f} |"
        )
    return "\n".join(lines) + "\n"


def _read_rss_kib(pid: int) -> int:
    total = 0
    seen: set[int] = set()

    def one(target: int) -> None:
        nonlocal total
        if target in seen:
            return
        seen.add(target)
        try:
            text = Path(f"/proc/{target}/status").read_text(encoding="utf-8")
        except OSError:
            return
        for line in text.splitlines():
            if line.startswith("VmRSS:"):
                total += int(line.split()[1])
                break

    one(pid)
    proc = Path("/proc")
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        try:
            stat = (child / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        # pid (comm) state ppid ...
        end = stat.rfind(")")
        if end < 0:
            continue
        parts = stat[end + 2 :].split()
        if parts and parts[1] == str(pid):
            one(int(child.name))
    return total


def _poll_peaks(pid: int, stop: threading.Event, peaks: dict[str, int]) -> None:
    while not stop.is_set():
        rss_mib = _read_rss_kib(pid) // 1024
        peaks["rss"] = max(peaks["rss"], rss_mib)
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                used = int(out.stdout.strip().splitlines()[0])
                peaks["vram"] = max(peaks["vram"], used)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        stop.wait(0.4)


def _wait_until_up(port: int, timeout_s: float) -> None:
    deadline = time.perf_counter() + timeout_s
    urls = (f"http://127.0.0.1:{port}/health", f"http://127.0.0.1:{port}/v1/models")
    while time.perf_counter() < deadline:
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status < 500:
                        return
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
        time.sleep(0.5)
    raise TimeoutError(f"server on 127.0.0.1:{port} did not come up within {timeout_s:.0f}s")


def _complete(port: int, prompt: Prompt) -> PromptResult:
    payload = {
        "model": "bench",
        "messages": [{"role": "user", "content": prompt.text}],
        "temperature": 0,
        "max_tokens": DECODE_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
            # A warm KV cache makes later prompts look shorter than they are.
            "cache_prompt": False,
        }
    try:
        return _stream(port, prompt, payload)
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 422}:
            raise
        payload.pop("stream_options", None)
        return _stream(port, prompt, payload)


def _stream(port: int, prompt: Prompt, payload: dict[str, object]) -> PromptResult:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first: float | None = None
    prompt_tokens = 0
    completion_tokens = 0
    pieces = 0
    with urllib.request.urlopen(req, timeout=LOAD_TIMEOUT_S) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            usage = data.get("usage") or {}
            if usage.get("prompt_tokens"):
                prompt_tokens = int(usage["prompt_tokens"])
            if usage.get("completion_tokens"):
                completion_tokens = int(usage["completion_tokens"])
            choices = data.get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices else {}
            content = delta.get("content") or ""
            if content and first is None:
                first = time.perf_counter()
            if content:
                pieces += 1
    finished = time.perf_counter()
    if first is None:
        first = finished
    if completion_tokens <= 0:
        completion_tokens = pieces
    decode_window = max(finished - first, 1e-3)
    return PromptResult(
        prompt=prompt,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        ttft_s=first - started,
        decode_tok_s=completion_tokens / decode_window,
    )


def run_bench(argv: list[str], port: int) -> tuple[float, dict[str, int], list[PromptResult]]:
    reject_public_bind(argv)
    log_dir = Path.home() / "pablo-data" / "benchmarks"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"bench-{port}-{int(time.time())}.log"
    log = log_path.open("w", encoding="utf-8")
    started = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    peaks = {"vram": 0, "rss": 0}
    stop = threading.Event()
    poller = threading.Thread(target=_poll_peaks, args=(proc.pid, stop, peaks), daemon=True)
    poller.start()
    rows: list[PromptResult] = []
    try:
        _wait_until_up(port, LOAD_TIMEOUT_S)
        load_s = time.perf_counter() - started
        for prompt in prompts():
            print(f"prompt {prompt.name}", flush=True)
            rows.append(_complete(port, prompt))
        return load_s, peaks, rows
    finally:
        stop.set()
        poller.join(timeout=2)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        log.close()
        print(f"server log: {log_path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bench a local OpenAI-compatible engine")
    parser.add_argument("--label", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--append", type=Path, help="Append the markdown table to this file")
    parser.add_argument("--self-check", action="store_true", help="Check prompts and exit")
    parser.add_argument("server", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.self_check:
        found = prompts()
        kinds = {p.kind for p in found}
        if kinds != {"judge", "code", "long"} or len(found) != 5:
            raise SystemExit(f"expected 5 prompts covering judge, code, long; got {kinds}")
        long = next(p for p in found if p.kind == "long")
        if len(long.text) < 24000:
            raise SystemExit("long prompt is shorter than ~8k tokens")
        print(f"{len(found)} prompts ok")
        return 0
    server = args.server
    if server and server[0] == "--":
        server = server[1:]
    if not server:
        raise SystemExit("pass the server command after --")
    load_s, peaks, rows = run_bench(server, args.port)
    table = render_table(args.label, load_s, peaks["vram"], peaks["rss"], rows)
    sys.stdout.write("\n" + table)
    if args.append:
        with args.append.open("a", encoding="utf-8") as fh:
            fh.write("\n" + table)
        print(f"appended to {args.append}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
