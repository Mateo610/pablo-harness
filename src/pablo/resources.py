"""What Pablo's hardware is doing right now: GPU, VRAM, RAM.

Reads `nvidia-smi` and /proc/meminfo, nothing heavier. `loaded` (which lanes are
resident) is filled in by the lane swapper in M4; until then it's empty.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GPU_QUERY = "name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw"


@dataclass
class Gpu:
    name: str
    vram_used_mb: int
    vram_total_mb: int
    util_pct: int
    temp_c: int
    power_w: float


@dataclass
class Resources:
    gpus: list[Gpu] = field(default_factory=list)
    ram_used_mb: int = 0
    ram_total_mb: int = 0
    loaded: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: str, cast: Callable[[str], Any] = int) -> Any:
    try:
        return cast(value.strip().split()[0])
    except (ValueError, IndexError):
        return cast("0")


def parse_nvidia_smi(output: str) -> list[Gpu]:
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        gpus.append(
            Gpu(
                name=parts[0],
                vram_used_mb=_num(parts[1]),
                vram_total_mb=_num(parts[2]),
                util_pct=_num(parts[3]),
                temp_c=_num(parts[4]),
                power_w=_num(parts[5], float),
            )
        )
    return gpus


def parse_meminfo(text: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = _num(rest) // 1024
    total = values.get("MemTotal", 0)
    return total - values.get("MemAvailable", total), total


def read_nvidia_smi() -> str:
    if not shutil.which("nvidia-smi"):
        raise FileNotFoundError("nvidia-smi not found")
    return subprocess.run(
        ["nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
    ).stdout


def snapshot(
    smi: Callable[[], str] = read_nvidia_smi,
    meminfo: Callable[[], str] = lambda: Path("/proc/meminfo").read_text(),
) -> Resources:
    res = Resources()
    try:
        res.gpus = parse_nvidia_smi(smi())
    except (OSError, subprocess.SubprocessError) as exc:
        res.error = f"gpu: {exc}"
    try:
        res.ram_used_mb, res.ram_total_mb = parse_meminfo(meminfo())
    except OSError as exc:
        res.error = (res.error + "; " if res.error else "") + f"ram: {exc}"
    return res
