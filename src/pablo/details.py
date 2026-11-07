"""A run as plain text you can paste anywhere: what it was for, what happened, why.

Behind the needs-you card's Copy details and Send to chat (C14c), and Fix it's feedback: one
block with the task, the plan step, the question, the checks that failed with the end of their
output, and the files touched. No diff: it's long and the card shows it.
"""

from __future__ import annotations

from typing import Any

TAIL_LINES = 20
STATE_WORDS = {"needs_you": "needs you", "ready": "ready", "failed": "failed",
               "running": "running", "approved": "applied", "rejected": "rejected"}  # fmt: skip


def first_paragraph(task: str) -> str:
    """The task the user (or the planner) wrote, without the plan notes the harness adds."""
    return task.strip().split("\n\n", 1)[0].strip()


def run_details(run: Any, *, plan_goal: str = "", step: Any = None) -> str:
    lines = [f"Pablo run {run.id} · {STATE_WORDS.get(run.state, run.state)} · {run.lane}"
             + (f" · project {run.project}" if run.project else "")]  # fmt: skip
    if step is not None:
        lines.append(f"Plan: {plan_goal or 'untitled'} · step {step.id}: {step.title}")
    lines += ["", "Task:", first_paragraph(run.task)]
    why = run.question or run.judge_summary
    if why:
        lines += ["", "What happened:", why.strip()]
    v = run.verdict or {}
    if v and not v.get("applied", True):
        lines += ["", "The change didn't apply to the repo (git apply failed)."]
    failed = [c for c in v.get("checks", []) if not c.get("passed")]
    passed = [c["name"] for c in v.get("checks", []) if c.get("passed")]
    if passed:
        lines += ["", "Checks that passed: " + ", ".join(passed)]
    for check in failed:
        tail = "\n".join(str(check.get("output_tail", "")).strip().splitlines()[-TAIL_LINES:])
        status = check.get("status") or "failed"
        lines += ["", f"Check {check.get('name', '?')}: {status}"]
        if tail:
            lines += ["```", tail, "```"]
    files = v.get("files") or []
    if files:
        lines += ["", f"Files: {', '.join(files)} (+{v.get('added', 0)} −{v.get('removed', 0)})"]
    elif run.state in ("needs_you", "failed") and not run.diff:
        lines += ["", "No change was made."]
    if run.certainty is not None:
        lines.append(f"Judge certainty: {run.certainty:.2f}")
    return "\n".join(lines).strip() + "\n"
