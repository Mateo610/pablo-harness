from __future__ import annotations

from pablo.resources import parse_meminfo, parse_nvidia_smi, snapshot

SMI = "NVIDIA GeForce RTX 3090, 21430, 24576, 87, 71, 312.45\n"
MEMINFO = "MemTotal:       65843200 kB\nMemFree:  1000 kB\nMemAvailable:   23887872 kB\n"


def test_parse_nvidia_smi() -> None:
    [gpu] = parse_nvidia_smi(SMI)
    assert gpu.name == "NVIDIA GeForce RTX 3090"
    assert (gpu.vram_used_mb, gpu.vram_total_mb, gpu.util_pct, gpu.temp_c) == (21430, 24576, 87, 71)
    assert gpu.power_w == 312.45


def test_parse_nvidia_smi_handles_na() -> None:
    [gpu] = parse_nvidia_smi("RTX 3090, 100, 24576, [N/A], 40, [N/A]\n")
    assert gpu.util_pct == 0 and gpu.power_w == 0.0


def test_parse_meminfo() -> None:
    used, total = parse_meminfo(MEMINFO)
    assert total == 64300 and used == 64300 - 23328


def test_snapshot_survives_missing_gpu() -> None:
    def no_gpu() -> str:
        raise FileNotFoundError("nvidia-smi not found")

    res = snapshot(no_gpu, lambda: MEMINFO)
    assert res.gpus == [] and "nvidia-smi" in res.error and res.ram_total_mb == 64300
