from __future__ import annotations

from pathlib import Path

import pytest

from pablo.config import load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_load_settings_reads_env_file_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PABLO_GATEWAY_KEY", raising=False)
    cfg = tmp_path / "lanes.yaml"
    cfg.write_text((ROOT / "config" / "lanes.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / ".env").write_text("PABLO_GATEWAY_KEY=sk-from-file\n", encoding="utf-8")

    assert load_settings(cfg).gateway.api_key == "sk-from-file"

    monkeypatch.setenv("PABLO_GATEWAY_KEY", "already-set")
    assert load_settings(cfg).gateway.api_key == "already-set"
