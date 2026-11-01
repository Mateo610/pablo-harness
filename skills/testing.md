---
name: testing
when: [test]
---
# pytest
- One behavior per test; name it `test_<unit>_<case>`. Plain `assert`, no assertion helpers.
- Cover the edge that the task is about: empty input, boundaries, the bug's exact input.
- Use `pytest.raises(ErrorType)` for errors; `@pytest.mark.parametrize` for tables of cases.
- Fixtures over setup code; `tmp_path` for files; never touch the real network or home dir.
- A new test must import every name it uses, and must fail before the fix.
- Never weaken or delete an existing assertion to make a run pass.
```python
@pytest.mark.parametrize("text,expected", [("", 0), ("a  b", 2)])
def test_word_count_edges(text: str, expected: int) -> None:
    assert word_count(text) == expected
```
