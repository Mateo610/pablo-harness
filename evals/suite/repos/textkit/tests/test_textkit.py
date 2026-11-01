from textkit.slug import slugify
from textkit.stats import top_words, word_count
from textkit.wrap import truncate, wrap


def test_slugify() -> None:
    assert slugify("Hello, World!") == "hello-world"


def test_wrap() -> None:
    assert wrap("the quick brown fox", 10) == ["the quick", "brown fox"]


def test_truncate() -> None:
    assert truncate("hi", 10) == "hi"
    assert truncate("abcdefghij", 4) == "abcd..."


def test_stats() -> None:
    assert word_count("hello world") == 2
    assert top_words("b a b", 1) == ["b"]
