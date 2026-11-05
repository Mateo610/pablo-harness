"""Numbers for the cockpit: the repo's state and what each lane spent.

Read-only. Token counts come from traces (runs) and from the chat usage table (chat turns
are not traces, so their text is never copied anywhere; only the counts are).
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pablo.orchestrator import Orchestrator


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def public_remote(url: str) -> str:
    """An https link for the remote, with any credentials removed. "" if there isn't one."""
    url = url.strip()
    if m := re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url):
        return f"https://{m.group(1)}/{m.group(2)}"
    if m := re.match(r"https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?$", url):
        return f"https://{m.group(1)}/{m.group(2)}"
    return ""


def _shortstat(text: str) -> tuple[int, int, int]:
    nums = [0, 0, 0]
    for i, word in enumerate(("file", "insertion", "deletion")):
        if m := re.search(rf"(\d+) {word}", text):
            nums[i] = int(m.group(1))
    return nums[0], nums[1], nums[2]


def repo_status(repo: Path) -> dict[str, Any]:
    if not _git(repo, "rev-parse", "--git-dir"):
        return {"name": repo.name, "git": False}
    upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    base = upstream or "HEAD"
    files, added, removed = _shortstat(_git(repo, "diff", "--shortstat", base))
    changed = []
    for line in _git(repo, "diff", "--numstat", base).splitlines():
        if line.count("\t") < 2:
            continue
        a, r, path = line.split("\t", 2)
        changed.append({"path": path, "added": int(a) if a.isdigit() else 0,
                        "removed": int(r) if r.isdigit() else 0})  # fmt: skip
    ahead = _git(repo, "rev-list", "--count", f"{upstream}..HEAD") if upstream else ""
    return {
        "name": repo.name,
        "git": True,
        "branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git(repo, "rev-parse", "--short", "HEAD"),
        "remote": public_remote(_git(repo, "remote", "get-url", "origin")),
        "upstream": upstream,
        "ahead": int(ahead) if ahead.isdigit() else 0,
        "since_push": {"files": files, "added": added, "removed": removed, "changed": changed[:20]},
    }


def lane_stats(
    orch: Orchestrator, days: int = 1, chat_usage: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    stamp = since.isoformat(timespec="seconds")
    lanes: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "approved": 0, "ready": 0, "needs_you": 0, "failed": 0,
                 "tokens_in": 0, "tokens_out": 0, "chat_tokens_in": 0, "chat_tokens_out": 0,
                 "latency_s": 0.0, "calls": 0}
    )  # fmt: skip
    for trace in orch.traces.iter_traces():
        if trace.at < stamp:
            continue
        row = lanes[trace.lane]
        row["tokens_in"] += trace.prompt_tokens
        row["tokens_out"] += trace.completion_tokens
        row["latency_s"] += trace.latency_s
        row["calls"] += 1
    for use in chat_usage or []:
        row = lanes[use["lane"]]
        row["chat_tokens_in"] += use["tokens_in"]
        row["chat_tokens_out"] += use["tokens_out"]
    runs = [r for r in orch.recent(500) if r.created_at >= stamp]
    for run in runs:
        row = lanes[run.lane]
        row["runs"] += 1
        if run.state in ("approved", "ready", "needs_you", "failed"):
            row[run.state] += 1
    for row in lanes.values():
        row["latency_s"] = round(row["latency_s"] / row["calls"], 1) if row["calls"] else 0.0
    return {
        "days": days,
        "lanes": dict(lanes),
        "runs": len(runs),
        "approved": sum(r.state == "approved" for r in runs),
        "retried": sum(bool(r.retry_run) for r in runs),
        "claude": sum(r.lane == "claude" for r in runs),
    }
