"""scripts/verify-archive.sh checks every SHA256SUMS under an archive root."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-archive.sh"


def _write_artifact(dest: Path, name: str, body: bytes) -> None:
    dest.mkdir(parents=True)
    (dest / name).write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    (dest / "SHA256SUMS").write_text(f"{digest}  {name}\n", encoding="utf-8")
    (dest / "SOURCE.txt").write_text(f"name: {name}\narchived: test\n", encoding="utf-8")


def test_verify_archive_passes_on_a_temp_tree(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    _write_artifact(archive / "models" / "toy", "weights.bin", b"abc")
    _write_artifact(archive / "engines" / "tool", "tool.bin", b"xyz")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "2 directories ok" in proc.stdout
    assert "ok  models/toy" in proc.stdout
    assert "ok  engines/tool" in proc.stdout


def test_verify_archive_fails_when_a_file_is_corrupt(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    dest = archive / "broken"
    _write_artifact(dest, "payload.bin", b"good")
    (dest / "payload.bin").write_bytes(b"tampered")
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "BAD" in proc.stderr
