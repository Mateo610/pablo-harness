"""Money is kept in integer cents."""


def parse_dollars(text: str) -> int:
    """'$1.50' -> 150."""
    clean = text.strip().lstrip("$")
    dollars, _, cents = clean.partition(".")
    return int(dollars) * 100 + int((cents + "00")[:2])


def format_cents(cents: int) -> str:
    """150 -> '$1.50'."""
    return f"${cents // 100}.{cents % 100:02d}"
