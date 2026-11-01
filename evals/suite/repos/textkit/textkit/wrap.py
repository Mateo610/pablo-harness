"""Fitting text into a width."""


def wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap. A word longer than width gets a line of its own."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return lines


def truncate(text: str, limit: int) -> str:
    """Cut to `limit` characters and add '...' when anything was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
