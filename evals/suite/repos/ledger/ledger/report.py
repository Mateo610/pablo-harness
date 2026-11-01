"""Totals and summaries."""

from ledger.entries import Entry, in_month
from ledger.money import format_cents


def total(entries: list[Entry]) -> int:
    return sum(e.cents for e in entries)


def monthly(entries: list[Entry], month: str) -> str:
    chosen = [e for e in entries if in_month(e, month)]
    return f"{month} {format_cents(total(chosen))}"


def by_payee(entries: list[Entry]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in entries:
        out[e.payee] = e.cents
    return out
