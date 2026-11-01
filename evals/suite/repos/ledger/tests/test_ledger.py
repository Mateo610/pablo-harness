from ledger.entries import Entry, load, load_line
from ledger.money import format_cents, parse_dollars
from ledger.report import monthly, total

TEXT = """2026-10-30, Grocer, $12.00
2026-11-02, Cafe, $1.50
2026-11-03, Grocer, $20.25
"""


def test_money_round_trip() -> None:
    assert parse_dollars("$1.50") == 150
    assert parse_dollars("$3") == 300
    assert format_cents(150) == "$1.50"
    assert format_cents(2025) == "$20.25"


def test_load_line() -> None:
    assert load_line("2026-11-02, Cafe, $1.50") == Entry("2026-11-02", "Cafe", 150)


def test_total_and_monthly() -> None:
    entries = load(TEXT)
    assert total(entries) == 3375
    assert monthly(entries, "2026-11") == "2026-11 $21.75"
