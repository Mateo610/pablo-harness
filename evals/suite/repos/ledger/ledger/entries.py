"""One entry per line: 'YYYY-MM-DD, payee, $amount'."""

from dataclasses import dataclass

from ledger.money import parse_dollars


@dataclass(frozen=True)
class Entry:
    day: str
    payee: str
    cents: int


def load_line(line: str) -> Entry:
    day, payee, dollars = line.split(",")
    return Entry(day.strip(), payee.strip(), parse_dollars(dollars))


def load(text: str) -> list[Entry]:
    return [load_line(line) for line in text.splitlines() if line.strip()]


def in_month(entry: Entry, month: str) -> bool:
    """month is 'YYYY-MM'."""
    return entry.day[:7] == month
