"""Files you hand Pablo to keep: dropped on the "remember" rail in Chat.

They sit next to your notes, per profile, under `<home>/files/<profile>/`. Nothing here sends
them anywhere: plans read them as context (C14b), and you can forget one at any time.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")
_DIR_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class KeptFile:
    id: str
    name: str
    size: int
    created_at: str


def safe_name(name: str) -> str:
    """A file name with no path in it, no hidden-file dot, and nothing a shell would trip on."""
    base = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return cleaned[:120] or "file"


class FileStore:
    def __init__(self, home: Path) -> None:
        self.root = home / "files"

    def _dir(self, profile: str) -> Path:
        # A teammate's profile is their Tailscale login ("ana@github"): readable, but never a path.
        if not profile:
            raise ValueError("no profile")
        digest = hashlib.sha256(profile.encode()).hexdigest()[:8]
        return self.root / f"{_DIR_SAFE.sub('_', profile)[:40]}-{digest}"

    def _index(self, profile: str) -> Path:
        return self._dir(profile) / "index.json"

    def _load(self, profile: str) -> list[KeptFile]:
        path = self._index(profile)
        if not path.is_file():
            return []
        return [KeptFile(**row) for row in json.loads(path.read_text("utf-8"))]

    def _save(self, profile: str, files: list[KeptFile]) -> None:
        path = self._index(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(f) for f in files], indent=1), "utf-8")
        tmp.replace(path)

    def list(self, profile: str) -> list[KeptFile]:
        return self._load(profile)

    def add(self, profile: str, name: str, data: bytes) -> KeptFile:
        if not data:
            raise ValueError("the file is empty")
        if len(data) > MAX_BYTES:
            raise ValueError(f"files are kept up to {MAX_BYTES // (1024 * 1024)} MB")
        kept = KeptFile(
            id=uuid.uuid4().hex[:12],
            name=safe_name(name),
            size=len(data),
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        folder = self._dir(profile)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{kept.id}-{kept.name}").write_bytes(data)
        self._save(profile, [*self._load(profile), kept])
        return kept

    def path(self, profile: str, fid: str) -> Path:
        found = next((f for f in self._load(profile) if f.id == fid), None)
        if found is None:
            raise KeyError(fid)
        return self._dir(profile) / f"{found.id}-{found.name}"

    def forget(self, profile: str, fid: str) -> None:
        self.path(profile, fid).unlink(missing_ok=True)
        self._save(profile, [f for f in self._load(profile) if f.id != fid])
