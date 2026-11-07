"""`pablo eval-chat` records what the model chose, without running anything."""

from __future__ import annotations

from collections.abc import Sequence

from tests.fakes import FakeBackend

from pablo.chat_eval import chat_markdown, chat_summary, load_chat_set, run_chat_eval
from pablo.config import Settings
from pablo.gateway import Message


def test_the_set_is_balanced() -> None:
    repo, cases = load_chat_set()
    assert repo == "ledger" and len(cases) >= 20
    assert {c.label for c in cases} == {"talk", "run", "plan"}
    assert min(sum(c.label == lab for c in cases) for lab in ("talk", "run", "plan")) >= 6


def test_it_scores_choices_and_never_runs_them(settings: Settings) -> None:
    def chat(model: str, msgs: Sequence[Message]) -> str:
        text = msgs[-1]["content"].lower()
        if "budget" in text or "tool" in text:
            return "PLAN: budgets\nSTEP: add a budget table\nSTEP: warn when over"
        if text.startswith(("rename", "make ", "add ", "sort")):
            return "On it.\nRUN: " + text
        return "It reads one line into an Entry."

    backend = FakeBackend(scripts={"pablo/coder": chat})
    cases = run_chat_eval(settings, backend)

    def chose(start: str) -> str:
        return next(c.chose for c in cases if c.text.startswith(start))

    assert chose("What does load_line") == "talk"
    assert chose("Make load skip") == "run"
    assert chose("Make parse_dollars") == "run"
    assert chose("Add multi-currency") == "run"  # a plan the fake got wrong: counted as a miss
    assert all(m == "pablo/coder" for m, _ in backend.calls)  # replies only: nothing was built
    summary = chat_summary(cases, lane="auto", spoken=False, commit="abc")
    assert summary["confusion"]["plan"]["run"] >= 1 and 0 < summary["accuracy"] < 1
    assert "| plan |" in chat_markdown(summary)
