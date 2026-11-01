"""Counting words."""


def word_count(text: str) -> int:
    return len(text.split(" "))


def top_words(text: str, n: int) -> list[str]:
    """The n most frequent words, most frequent first."""
    counts: dict[str, int] = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts, key=lambda w: counts[w], reverse=True)[:n]
