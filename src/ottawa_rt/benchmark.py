from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from ottawa_rt.config import Settings


@dataclass(frozen=True)
class CandidateEstimate:
    width_m: int
    tile_count: int
    estimated_hours: float
    estimated_disk_gb: float
    estimated_peak_vram_gb: float
    accepted: bool
    reason: str


def estimate_candidates(
    settings: Settings,
    *,
    measured_seconds_per_tile_band: float = 120.0,
    measured_scene_vram_gb: float = 2.0,
    measured_width_m: float = 2000.0,
    band_count: int = 4,
) -> dict[str, object]:
    area, compute = settings.raw["area"], settings.raw["compute"]
    budget = float(compute["total_budget_hours"]) - float(compute["preparation_reserve_hours"])
    workers = max(int(compute["local_gpu_count"]), 1)
    max_vram = 8.0 * float(compute["max_vram_fraction"])
    tile_size = float(area["tile_size_m"])
    free_gb = (
        shutil.disk_usage(settings.paths.data).free / (1024**3)
        if settings.paths.data.exists()
        else 100.0
    )
    candidates: list[CandidateEstimate] = []
    selected = None
    for width in range(
        int(area["min_width_m"]), int(area["max_width_m"]) + 1, int(area["step_width_m"])
    ):
        tiles_per_side = int(-(-width // tile_size))
        tile_count = tiles_per_side**2
        hours = tile_count * band_count * measured_seconds_per_tile_band / 3600.0 / workers
        # Geometry growth is approximately proportional to area; leave ray buffers to runtime profiling.
        vram = measured_scene_vram_gb * (width / measured_width_m) ** 2
        disk = tile_count * band_count * 0.015 + (width / 1000.0) ** 2 * 0.1
        accepted = hours <= budget and vram <= max_vram and disk <= free_gb * 0.7
        reasons = []
        if hours > budget:
            reasons.append("runtime")
        if vram > max_vram:
            reasons.append("vram")
        if disk > free_gb * 0.7:
            reasons.append("disk")
        item = CandidateEstimate(
            width, tile_count, hours, disk, vram, accepted, ",".join(reasons) or "ok"
        )
        candidates.append(item)
        if accepted:
            selected = width
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "method": "extrapolated; replace inputs with measured smoke-run metrics",
        "selected_width_m": selected,
        "inputs": {
            "seconds_per_tile_band": measured_seconds_per_tile_band,
            "scene_vram_gb": measured_scene_vram_gb,
            "measured_width_m": measured_width_m,
            "band_count": band_count,
            "workers": workers,
            "usable_vram_gb_per_worker": max_vram,
            "ray_trace_budget_hours": budget,
        },
        "candidates": [asdict(item) for item in candidates],
    }
    output = settings.paths.processed / "benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
