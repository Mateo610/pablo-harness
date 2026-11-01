from textutils import truncate, word_count


def test_word_count_simple() -> None:
    assert word_count("hello world") == 2


def test_truncate_short_text_unchanged() -> None:
    assert truncate("hi", 10) == "hi"


def test_truncate_long_text() -> None:
    assert truncate("abcdefghij", 4) == "abcd..."
