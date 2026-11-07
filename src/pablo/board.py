"""The Work board (C14d): every chat's plan in every project, and where two of them collide.

Two chats can build in one project at once. The board shows each plan's progress and the step
it's on, and warns when two live plans have touched, or plan to touch, the same files, so
parallel chats don't overwrite each other's work. The owner sees every plan; a teammate sees
their own, and is still warned (without names) when someone else's plan shares their files.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pablo.plan import FINISHED, Plan
from pablo.plans import Plans

if TYPE_CHECKING:
    from pablo.convos import Store
    from pablo.projects import Workspace

LIVE = ("draft", "running", "paused")
DONE_FOR = timedelta(hours=24)  # a finished plan stays on the board for a day
# Paths a step names: "plantwatch/reader.py", "web/src/Chat.tsx". Not a parser; a good guess.
_PATH = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|md|toml|ya?ml|json|css|html"
    r"|rs|go|sh|sql|txt|cfg|ini))(?![\w/-])"
)


def named_paths(text: str) -> set[str]:
    return {m.group(1).removeprefix("./") for m in _PATH.finditer(text)}


def plan_files(plans: Plans, project: str, plan: Plan) -> tuple[set[str], set[str]]:
    """(files its steps changed, files its unfinished steps name)."""
    orch = plans.ws.orch(project)
    touched: set[str] = set()
    planned: set[str] = set()
    for step in Plans.steps(plan):
        if step.run:
            try:
                verdict = orch.get(step.run).verdict or {}
            except KeyError:
                verdict = {}
            touched |= set(verdict.get("files") or [])
        if step.status not in FINISHED:
            planned |= named_paths(step.detail or step.title)
    return touched, planned


def _recent(stamp: str, now: datetime) -> bool:
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return now - when <= DONE_FOR


def plan_board(
    ws: Workspace,
    plans: Plans,
    store: Store,
    *,
    viewer: str,
    owner: bool,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for project in ws.projects():
        found: list[tuple[str, Plan, set[str]]] = []
        for key, plan in plans.all(project.name):
            if not key or not Plans.steps(plan):
                continue  # the project's own plan.yaml lives on the Workbench below
            if plan.state == "done" and not _recent(plan.updated, now):
                continue
            touched, planned = plan_files(plans, project.name, plan)
            conv = store.find_conversation(key)
            mine = conv is not None and conv.profile_id == viewer
            files = touched | planned
            found.append((key, plan, files))
            if not (owner or mine):
                continue
            steps = Plans.steps(plan)
            current = next((t for t in steps if t.status not in FINISHED), None)
            rows.append(
                {
                    "project": project.name,
                    "project_title": project.title or project.name,
                    "conversation": key,
                    "title": conv.title if conv else "(a deleted chat)",
                    "by": conv.profile_id if conv else plan.by,
                    "mine": mine,
                    "goal": plan.goal,
                    "size": plan.size,
                    "state": plan.state,
                    "updated": plan.updated,
                    "steps": [{"id": t.id, "status": t.status} for t in steps],
                    "done": sum(t.status in FINISHED for t in steps),
                    "total": len(steps),
                    "current": {
                        "id": current.id,
                        "title": current.title,
                        "status": current.status,
                        "run": current.run,
                    }
                    if current
                    else None,
                    "touched": sorted(touched),
                    "planned": sorted(planned - touched),
                    "overlaps": [],
                }
            )
        # Collisions: live plans in one project that share files. Named only if you can see them.
        live = [(k, p, f) for k, p, f in found if p.state in LIVE]
        shown = {r["conversation"]: r for r in rows if r["project"] == project.name}
        for key, _, files in live:
            row = shown.get(key)
            if row is None or row["state"] not in LIVE:
                continue
            for other, _, theirs in live:
                shared = sorted(files & theirs)
                if other == key or not shared:
                    continue
                seen = shown.get(other)
                row["overlaps"].append(
                    {
                        "conversation": other if seen else "",
                        "title": seen["title"] if seen else "another chat",
                        "files": shared,
                    }
                )
    # Building first, then paused, drafts, and done; newest first within each.
    rank = {"running": 0, "paused": 1, "draft": 2, "done": 3}
    rows.sort(key=lambda r: r["updated"], reverse=True)
    rows.sort(key=lambda r: rank.get(r["state"], 4))
    return rows
