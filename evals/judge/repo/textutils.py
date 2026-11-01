"""Small text helpers used by the judge fixtures."""


def word_count(text: str) -> int:
    return len(text.split(" "))


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
