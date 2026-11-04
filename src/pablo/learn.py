"""Nightly learning loop: export → train LoRA → eval candidate → promotion inbox.

Promotion is never automatic: a pending file lands in `~/.pablo/promotions/` and
you promote or reject it. Training refuses any row whose lane is `claude`.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pablo.config import ESCALATION_LANE, default_home
from pablo.traces import TraceStore

MIN_NEW_EXAMPLES = 50
HOLDOUT_FRACTION = 0.10
PABLO_DATA = Path(os.environ.get("PABLO_DATA", Path.home() / "pablo-data"))


@dataclass
class TrainingRow:
    messages: list[dict[str, str]]
    lane: str
    prompt_hash: str = ""

    @classmethod
    def from_export(cls, row: dict[str, Any]) -> TrainingRow:
        messages = list(row.get("messages") or [])
        lane = str(row.get("lane") or "")
        prompt = "\n".join(f"{m.get('role')}:{m.get('content')}" for m in messages[:-1])
        return cls(
            messages=messages,
            lane=lane,
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
        )


@dataclass
class PreparedData:
    train_path: Path
    holdout_path: Path
    train_n: int
    holdout_n: int
    skipped_dupes: int


@dataclass
class Promotion:
    id: str
    created_at: str
    adapter: str
    baseline_pass_rate: float
    candidate_pass_rate: float
    baseline_commit: str
    candidate_wall_s: float
    beats_baseline: bool
    state: str = "pending"  # pending | promoted | rejected | held
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Promotion:
        fields = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        fields.setdefault("beats_baseline", False)
        return cls(**fields)


class ClaudeInTrainingError(ValueError):
    """Training data included an escalation-lane row."""


def promotions_dir(home: Path | None = None) -> Path:
    return (home or default_home()) / "promotions"


def adapters_dir() -> Path:
    return PABLO_DATA / "adapters"


def unsloth_python() -> Path:
    return PABLO_DATA / "engines" / "unsloth" / "bin" / "python"


def train_marker(home: Path | None = None) -> Path:
    return (home or default_home()) / "learn" / "last_train"


def load_export(path: Path) -> list[TrainingRow]:
    rows: list[TrainingRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(TrainingRow.from_export(json.loads(line)))
    return rows


def assert_no_claude(rows: list[TrainingRow]) -> None:
    bad = [r for r in rows if r.lane == ESCALATION_LANE or r.lane == "claude"]
    if bad:
        raise ClaudeInTrainingError(
            f"refusing to train: {len(bad)} row(s) from the escalation lane"
        )


def prepare_training(
    export_path: Path, out_dir: Path, *, seed: int = 7
) -> PreparedData:
    """Dedupe by prompt hash, hold out 10%, write train/holdout JSONL."""
    rows = load_export(export_path)
    assert_no_claude(rows)
    seen: set[str] = set()
    unique: list[TrainingRow] = []
    skipped = 0
    for row in rows:
        if row.prompt_hash in seen:
            skipped += 1
            continue
        seen.add(row.prompt_hash)
        unique.append(row)
    rng = random.Random(seed)
    rng.shuffle(unique)
    n_hold = max(1, int(len(unique) * HOLDOUT_FRACTION)) if len(unique) >= 10 else 0
    holdout = unique[:n_hold]
    train = unique[n_hold:] or list(unique)
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path = out_dir / "train.jsonl"
    holdout_path = out_dir / "holdout.jsonl"
    _write_rows(train_path, train)
    _write_rows(holdout_path, holdout)
    return PreparedData(
        train_path=train_path,
        holdout_path=holdout_path,
        train_n=len(train),
        holdout_n=len(holdout),
        skipped_dupes=skipped,
    )


def _write_rows(path: Path, rows: list[TrainingRow]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(
                json.dumps(
                    {
                        "messages": row.messages,
                        "lane": row.lane,
                        "prompt_hash": row.prompt_hash,
                    }
                )
                + "\n"
            )


def count_new_since(store: TraceStore, marker: Path) -> int:
    """Approved trainable traces newer than the last successful train marker."""
    since = 0.0
    if marker.is_file():
        try:
            since = float(marker.read_text().strip() or "0")
        except ValueError:
            since = 0.0
    n = 0
    for trace in store.iter_traces():
        if not trace.train_ok:
            continue
        try:
            ts = datetime.fromisoformat(trace.at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if ts > since:
            n += 1
    return n


def write_marker(marker: Path) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(time.time()), encoding="utf-8")


def save_promotion(promo: Promotion, home: Path | None = None) -> Path:
    folder = promotions_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{promo.id}.json"
    path.write_text(json.dumps(promo.to_dict(), indent=2) + "\n", encoding="utf-8")
    # Only surface as pending when it beats the baseline; otherwise held for inspection.
    pending = folder / "pending.json"
    if promo.beats_baseline and promo.state == "pending":
        pending.write_text(json.dumps(promo.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_pending(home: Path | None = None) -> Promotion | None:
    path = promotions_dir(home) / "pending.json"
    if not path.is_file():
        return None
    return Promotion.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_promotions(home: Path | None = None) -> list[Promotion]:
    folder = promotions_dir(home)
    if not folder.is_dir():
        return []
    out: list[Promotion] = []
    for path in sorted(folder.glob("*.json")):
        if path.name == "pending.json":
            continue
        out.append(Promotion.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return out


def set_promotion_state(home: Path, state: str, note: str = "") -> Promotion | None:
    pending = load_pending(home)
    if pending is None:
        return None
    pending.state = state
    pending.note = note
    path = promotions_dir(home) / f"{pending.id}.json"
    path.write_text(json.dumps(pending.to_dict(), indent=2) + "\n", encoding="utf-8")
    pending_path = promotions_dir(home) / "pending.json"
    if state == "pending":
        pending_path.write_text(json.dumps(pending.to_dict(), indent=2) + "\n", encoding="utf-8")
    elif pending_path.is_file():
        pending_path.unlink()
    return pending


def run_train_script(
    train_jsonl: Path,
    out_dir: Path,
    *,
    python: Path | None = None,
    script: Path | None = None,
    skip_train: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run scripts/train_lora.py in the Unsloth venv (separate process)."""
    py = python or unsloth_python()
    repo = Path(__file__).resolve().parents[2]
    train_script = script or (repo / "scripts" / "train_lora.py")
    if not py.is_file():
        raise FileNotFoundError(
            f"Unsloth python not found at {py}. Create $PABLO_DATA/engines/unsloth first."
        )
    cmd = [str(py), str(train_script), "--data", str(train_jsonl), "--out", str(out_dir)]
    if skip_train:
        cmd.append("--skip-train")
    log_path = out_dir / "train.log"
    out_dir.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, text=True, stdout=log, stderr=subprocess.STDOUT, check=False)
    # Re-read for callers that print stdout/stderr.
    text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    return subprocess.CompletedProcess(cmd, proc.returncode, text, "")


def baseline_pass_rate(results_path: Path = Path("evals/results.jsonl")) -> tuple[float, str]:
    """Latest coder suite aggregate mean, or the latest single coder run."""
    if not results_path.is_file():
        return 0.0, ""
    coder_rows = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("lane") == "coder":
            coder_rows.append(row)
    if not coder_rows:
        return 0.0, ""
    last = coder_rows[-1]
    if len(coder_rows) >= 3:
        trio = coder_rows[-3:]
        if all(r.get("tasks") == last.get("tasks") for r in trio):
            mean = sum(float(r["pass_rate"]) for r in trio) / 3
            return round(mean, 3), str(last.get("commit") or "")
    return float(last["pass_rate"]), str(last.get("commit") or "")


def new_adapter_dir(stamp: str | None = None) -> Path:
    day = stamp or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = adapters_dir() / f"coder-{day}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish_candidate(adapter_dir: Path) -> Path:
    """Point coder-candidate at this adapter (GGUF preferred)."""
    dest = adapters_dir() / "coder-candidate"
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    gguf = adapter_dir / "adapter.gguf"
    if gguf.is_file():
        shutil.copy2(gguf, dest / "adapter.gguf")
    else:
        # Keep PEFT tree for inspection; candidate serve needs GGUF.
        peft = adapter_dir / "adapter"
        if peft.is_dir():
            shutil.copytree(peft, dest / "adapter", dirs_exist_ok=True)
    (dest / "source").write_text(str(adapter_dir) + "\n", encoding="utf-8")
    return dest


def promote_adapter(home: Path | None = None) -> Promotion:
    """Swap candidate → active; keep previous active as coder-prev. Human-gated."""
    pending = load_pending(home)
    if pending is None:
        raise ValueError("no pending promotion")
    if not pending.beats_baseline:
        raise ValueError(
            f"candidate {pending.candidate_pass_rate:.0%} does not beat "
            f"baseline {pending.baseline_pass_rate:.0%}; refuse promote"
        )
    candidate = adapters_dir() / "coder-candidate"
    active = adapters_dir() / "coder-active"
    prev = adapters_dir() / "coder-prev"
    if not (candidate / "adapter.gguf").is_file():
        raise FileNotFoundError(f"missing {candidate / 'adapter.gguf'}")
    if active.exists():
        if prev.exists():
            shutil.rmtree(prev) if prev.is_dir() else prev.unlink()
        active.rename(prev)
    shutil.copytree(candidate, active)
    pending.state = "promoted"
    pending.note = f"active ← {pending.adapter}; prev kept at {prev}"
    path = promotions_dir(home) / f"{pending.id}.json"
    path.write_text(json.dumps(pending.to_dict(), indent=2) + "\n", encoding="utf-8")
    pending_path = promotions_dir(home) / "pending.json"
    if pending_path.is_file():
        pending_path.unlink()
    return pending


def reject_promotion(home: Path | None = None, note: str = "") -> Promotion:
    pending = set_promotion_state(home or default_home(), "rejected", note=note or "rejected")
    if pending is None:
        raise ValueError("no pending promotion")
    return pending


def should_run_nightly(store: TraceStore, home: Path | None = None) -> tuple[bool, str, int]:
    n = count_new_since(store, train_marker(home))
    if n < MIN_NEW_EXAMPLES:
        return False, f"only {n} new examples (need {MIN_NEW_EXAMPLES})", n
    return True, "", n
