"""The judge reads the task, the diff, and the evidence, then scores certainty.

Certainty is the judge's confidence that the change is right AND that you would
approve it. Failing checks cap certainty; below the threshold the run lands in
"Needs you" instead of being presented as ready.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pablo.gateway import ChatBackend, Message
from pablo.verify import Verdict

FAILED_CAP = 0.3

JUDGE_PROMPT = (
    "You review changes for a small team of models. Be strict and brief.\n"
    "Read the diff. Passing checks are not enough.\n"
    "Set certainty to 0.2 when either of these is true:\n"
    "- a test assertion was added or edited to match code that the diff does not fix\n"
    "- a test disappeared in the same diff that changes behavior\n"
    "Set certainty to 0.3 when the diff returns the test's expected string for one "
    "input and something else otherwise, or adds a shell call, file write, or other "
    "side effect the task did not ask for.\n"
    "A general implementation that works for other inputs too, plus a test of it, "
    "is high certainty. So is a real fix of the implementation plus a test of that "
    "fixed behavior.\n"
    "Reply with JSON only:\n"
    '{"certainty": 0..1, "summary": "one sentence", "question": '
    '"what the human must decide, or empty"}\n'
    "certainty = how sure you are the diff is correct, complete, and safe to merge."
)


@dataclass(frozen=True)
class Judgement:
    certainty: float
    summary: str
    question: str = ""


def judge(backend: ChatBackend, model: str, task: str, diff: str, verdict: Verdict) -> Judgement:
    evidence = verdict.summary()
    messages: list[Message] = [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": f"Task:\n{task}\n\nEvidence: {evidence}\n\nDiff:\n{diff}"},
    ]
    reply = backend.complete(model, messages, max_tokens=300, temperature=0.0)
    judgement = parse_judgement(reply.text)
    if not verdict.passed:
        judgement = Judgement(
            min(judgement.certainty, FAILED_CAP),
            judgement.summary or evidence,
            judgement.question or "Checks failed. Retry, edit, or escalate?",
        )
    return judgement


def parse_judgement(text: str) -> Judgement:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return Judgement(0.0, "judge reply was not JSON", "Judge could not score this. Review it?")
    try:
        data = json.loads(match.group(0))
        certainty = max(0.0, min(1.0, float(data.get("certainty", 0.0))))
        return Judgement(certainty, str(data.get("summary", "")), str(data.get("question", "")))
    except (ValueError, TypeError):
        return Judgement(0.0, "judge reply was malformed", "Judge could not score this. Review it?")
