from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bench():
    spec = importlib.util.spec_from_file_location("pablo_bench", ROOT / "scripts" / "bench.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pablo_bench"] = module
    spec.loader.exec_module(module)
    return module


def test_five_prompts_cover_judge_code_and_long_context() -> None:
    bench = _bench()
    found = bench.prompts()
    assert [p.kind for p in found] == ["judge", "judge", "code", "code", "long"]
    long = found[-1]
    assert len(long.text) >= 24000
    assert "certainty" in found[0].text


def test_public_bind_is_refused() -> None:
    bench = _bench()
    try:
        bench.reject_public_bind(["llama-server", "--host", "0.0.0.0", "--port", "8003"])
    except SystemExit as exc:
        assert "0.0.0.0" in str(exc)
    else:
        raise AssertionError("0.0.0.0 was allowed")
    bench.reject_public_bind(["llama-server", "--host", "127.0.0.1", "--port", "8003"])


def test_table_lists_every_prompt() -> None:
    bench = _bench()
    prompts = bench.prompts()
    rows = [
        bench.PromptResult(
            prompt=p, prompt_tokens=8, completion_tokens=4, ttft_s=0.2, decode_tok_s=10.0
        )
        for p in prompts
    ]
    table = bench.render_table("unit", 3.0, 1000, 2000, rows)
    assert "Load 3.0 s" in table
    assert table.count("\n| ") == 6  # header plus five prompts
