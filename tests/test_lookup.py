"""Symbol lookup for the two-step coder: parse, local defs/refs, process boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.fakes import FakeBackend

from pablo.config import Settings
from pablo.lookup import InlineLookup, ProcessLookup, local_lookup, parse_symbol_request
from pablo.orchestrator import Orchestrator


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "store.py").write_text(
        'def remove(sku: str, qty: int) -> None:\n    """Drop qty units."""\n    pass\n'
    )
    (root / "pkg" / "api.py").write_text("from pkg.store import remove\n\nremove('a', 1)\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )
    return root


def test_parse_symbol_request_reads_json_list() -> None:
    assert parse_symbol_request('Need ["remove", "qty"] next.') == ["remove", "qty"]
    assert parse_symbol_request("no list here") is None
    assert parse_symbol_request("[]") is None


def test_local_lookup_finds_definition_and_reference(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    result = local_lookup(repo, ["remove"], home=tmp_path / "home")
    kinds = {(h.kind, h.path) for h in result.hits}
    assert ("definition", "pkg/store.py") in kinds
    assert ("reference", "pkg/api.py") in kinds


def test_process_lookup_matches_inline(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    via_proc = ProcessLookup().lookup(repo, ["remove"], home=home)
    via_inline = InlineLookup().lookup(repo, ["remove"], home=home)
    assert via_proc.error == ""
    assert {h.path for h in via_proc.hits} == {h.path for h in via_inline.hits}


def test_two_step_coder_looks_up_then_edits(settings: Settings, tmp_path: Path) -> None:
    settings.lane("coder").symbol_lookup = True
    settings.lane("coder").fix_rounds = 0
    repo = _repo(tmp_path)
    (repo / "pkg" / "store.py").write_text(
        "def remove(sku: str, qty: int) -> None:\n    if qty < 0:\n        raise ValueError\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    calls: list[str] = []

    def coder(_model: str, messages: list) -> str:  # type: ignore[type-arg]
        calls.append(messages[0]["content"][:40])
        if len(calls) == 1:
            return '["remove"]'
        assert any("definition remove" in m["content"] for m in messages if m["role"] == "user")
        return (
            "Raise on negative qty.\n\n"
            "pkg/store.py\n<<<<<<< SEARCH\n"
            "def remove(sku: str, qty: int) -> None:\n    if qty < 0:\n        raise ValueError\n"
            "=======\n"
            "def remove(sku: str, qty: int) -> None:\n"
            "    if qty < 0:\n        raise ValueError('qty')\n"
            ">>>>>>> REPLACE\n"
        )

    backend = FakeBackend(
        scripts={
            "pablo/coder": coder,
            "pablo/judge": lambda m, msgs: json.dumps(
                {"certainty": 0.9, "summary": "ok", "question": ""}
            ),
        }
    )
    orch = Orchestrator(
        settings,
        backend,
        repo=repo,
        checks={"tests": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]},
        lookup=InlineLookup(),
    )
    run = orch.start("remove must reject a negative quantity", lane="coder")
    assert len(calls) == 2
    assert run.diff
    events = orch.events(run.id)
    assert any(e["kind"] == "lookup" and e["data"].get("symbols") == ["remove"] for e in events)
