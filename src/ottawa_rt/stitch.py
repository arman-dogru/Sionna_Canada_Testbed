from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.geo import bbox_from_center

TILE_PATTERN = re.compile(r"r(?P<row>\d+)-c(?P<column>\d+)-(?P<frequency>\d+p\d+)MHz\.npz$")


METRIC_SOURCES = {
    "max_path_gain_db": "path_gain_db",
    "max_rss_dbm": "rss_dbm",
    "max_rsrp_dbm": "rsrp_dbm",
    "max_sinr_db": "sinr_db",
}


def _maximum(data: object, output_name: str) -> np.ndarray:
    if output_name in data:
        return np.asarray(data[output_name], dtype=np.float32)
    values = np.asarray(data[METRIC_SOURCES[output_name]])
    finite = np.isfinite(values)
    safe = np.where(finite, values, -np.inf)
    return np.max(safe, axis=0).astype(np.float32)


def _finite_max(arrays: list[np.ndarray]) -> np.ndarray:
    """Take a maximum while preserving cells reached by any input layer."""
    stacked = np.stack(arrays)
    return np.max(np.where(np.isfinite(stacked), stacked, -np.inf), axis=0)


def _summary(data: object) -> tuple[dict[str, np.ndarray], np.ndarray]:
    metrics = {name: _maximum(data, name) for name in METRIC_SOURCES}
    rsrp = np.asarray(data["rsrp_dbm"])
    finite = np.isfinite(rsrp)
    safe = np.where(finite, rsrp, -np.inf)
    association = np.argmax(safe, axis=0)
    sectors = np.asarray(data["sector_ids"]).astype(str)
    serving = sectors[association]
    serving[~np.any(finite, axis=0)] = ""
    return metrics, serving


def _compact_summary(
    data: object,
    sector_to_index: dict[str, int],
    sector_ids: list[str],
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Return tile metrics with a compact serving-sector index grid.

    A full-resolution ``<U256`` sector-name grid is several gigabytes for the
    6 km / 2.5 m run. Store integer codes in the raster and a small lookup table
    instead. Index zero is reserved for cells with no received signal.
    """
    metrics = {name: _maximum(data, name) for name in METRIC_SOURCES}
    rsrp = np.asarray(data["rsrp_dbm"])
    finite = np.isfinite(rsrp)
    safe = np.where(finite, rsrp, -np.inf)
    association = np.argmax(safe, axis=0)
    local_sector_ids = np.asarray(data["sector_ids"]).astype(str)
    local_to_output = np.empty(local_sector_ids.size, dtype=np.uint32)
    for local_index, sector_id in enumerate(local_sector_ids):
        output_index = sector_to_index.get(sector_id)
        if output_index is None:
            output_index = len(sector_ids)
            sector_to_index[sector_id] = output_index
            sector_ids.append(sector_id)
        local_to_output[local_index] = output_index
    serving = local_to_output[association]
    serving[~np.any(finite, axis=0)] = 0
    return metrics, serving


def _sector_table(values: list[str]) -> np.ndarray:
    width = max(1, max((len(value) for value in values), default=0))
    return np.asarray(values, dtype=f"<U{width}")


def _strongest(data: object) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility helper returning the strongest RSRP and serving-sector layers."""
    strongest = _maximum(data, "max_rsrp_dbm")
    rsrp = np.asarray(data["rsrp_dbm"])
    finite = np.isfinite(rsrp)
    safe = np.where(finite, rsrp, -np.inf)
    association = np.argmax(safe, axis=0)
    sectors = np.asarray(data["sector_ids"]).astype(str)
    serving = sectors[association]
    serving[~np.any(finite, axis=0)] = ""
    return strongest, serving


def _seam_statistics(
    tiles: dict[tuple[int, int], np.ndarray], overlap_cells: int
) -> dict[str, object]:
    differences: list[np.ndarray] = []
    if overlap_cells <= 0:
        return {"sample_count": 0, "mae_db": None, "p95_abs_db": None, "max_abs_db": None}
    span = overlap_cells * 2
    for (row, column), values in tiles.items():
        right = tiles.get((row, column + 1))
        if right is not None:
            left_edge, right_edge = values[:, -span:], right[:, :span]
            mask = np.isfinite(left_edge) & np.isfinite(right_edge)
            if mask.any():
                differences.append(np.abs(left_edge[mask] - right_edge[mask]))
        above = tiles.get((row + 1, column))
        if above is not None:
            lower_edge, upper_edge = values[-span:, :], above[:span, :]
            mask = np.isfinite(lower_edge) & np.isfinite(upper_edge)
            if mask.any():
                differences.append(np.abs(lower_edge[mask] - upper_edge[mask]))
    if not differences:
        return {"sample_count": 0, "mae_db": None, "p95_abs_db": None, "max_abs_db": None}
    combined = np.concatenate(differences)
    return {
        "sample_count": int(combined.size),
        "mae_db": float(np.mean(combined)),
        "p95_abs_db": float(np.percentile(combined, 95)),
        "max_abs_db": float(np.max(combined)),
    }


def stitch_run(settings: Settings, run_id: str) -> dict[str, object]:
    run_dir = settings.paths.runs / run_id
    manifest_path = run_dir / "run.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Simulation run not found: {run_id}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    grouped: dict[float, list[tuple[Path, int, int]]] = defaultdict(list)
    for path in sorted((run_dir / "tiles").glob("*.npz")):
        match = TILE_PATTERN.match(path.name)
        if not match:
            continue
        frequency = float(match.group("frequency").replace("p", "."))
        grouped[frequency].append((path, int(match.group("row")), int(match.group("column"))))

    tile_count = int(manifest["tile_count"])
    side = round(math.sqrt(tile_count))
    if side * side != tile_count:
        raise ValueError(f"Run {run_id} has a non-square tile layout")
    output_dir = run_dir / "stitched"
    output_dir.mkdir(exist_ok=True)
    reports = []
    aggregate_metrics: dict[str, np.ndarray] | None = None
    aggregate_serving: np.ndarray | None = None
    aggregate_frequency: np.ndarray | None = None
    aggregate_sector_ids = [""]
    aggregate_sector_to_index = {"": 0}
    output_bbox = bbox_from_center(*settings.anchor, float(manifest["width_m"]))
    bounds_wgs84 = np.asarray(
        [output_bbox.west, output_bbox.south, output_bbox.east, output_bbox.north]
    )
    for frequency, paths in sorted(grouped.items()):
        if len(paths) != tile_count:
            reports.append(
                {"frequency_mhz": frequency, "status": "incomplete", "completed_tiles": len(paths)}
            )
            continue
        with np.load(paths[0][0], allow_pickle=False) as first:
            cell = float(first["cell_size_m"])
            tile_size = float(first["tile_size_m"])
            overlap = float(first["overlap_m"])
        core_cells = round(tile_size / cell)
        crop = round(overlap / cell)
        stitched_metrics = {
            name: np.full((side * core_cells, side * core_cells), -np.inf, dtype=np.float32)
            for name in METRIC_SOURCES
        }
        stitched_serving = np.zeros(next(iter(stitched_metrics.values())).shape, dtype=np.uint32)
        frequency_sector_ids = [""]
        frequency_sector_to_index = {"": 0}
        full_tiles: dict[tuple[int, int], np.ndarray] = {}
        for path, row, column in paths:
            with np.load(path, allow_pickle=False) as data:
                metrics, serving = _compact_summary(
                    data, frequency_sector_to_index, frequency_sector_ids
                )
            full_tiles[(row, column)] = metrics["max_rsrp_dbm"]
            core_serving = serving[crop : crop + core_cells, crop : crop + core_cells]
            row_slice = slice(row * core_cells, (row + 1) * core_cells)
            column_slice = slice(column * core_cells, (column + 1) * core_cells)
            for name, values in metrics.items():
                stitched_metrics[name][row_slice, column_slice] = values[
                    crop : crop + core_cells, crop : crop + core_cells
                ]
            stitched_serving[row_slice, column_slice] = core_serving
        output = output_dir / f"{frequency:.1f}MHz.npz"
        np.savez_compressed(
            output,
            **stitched_metrics,
            strongest_rsrp_dbm=stitched_metrics["max_rsrp_dbm"],
            serving_sector_index=stitched_serving,
            sector_ids=_sector_table(frequency_sector_ids),
            frequency_mhz=np.asarray(frequency),
            cell_size_m=np.asarray(cell),
            width_m=np.asarray(float(manifest["width_m"])),
            bounds_wgs84=bounds_wgs84,
            anchor_wgs84=np.asarray([settings.anchor[1], settings.anchor[0]]),
            visualization_schema_version=np.asarray(2),
        )
        seam = _seam_statistics(full_tiles, crop)
        reports.append(
            {
                "frequency_mhz": frequency,
                "status": "complete",
                "completed_tiles": len(paths),
                "output": settings.portable_path(output),
                "seam": seam,
            }
        )
        if aggregate_metrics is None:
            shape = stitched_metrics["max_rsrp_dbm"].shape
            aggregate_metrics = {
                name: np.full(shape, -np.inf, dtype=np.float32) for name in METRIC_SOURCES
            }
            aggregate_serving = np.zeros(shape, dtype=np.uint32)
            aggregate_frequency = np.full(shape, np.nan, dtype=np.float32)

        assert aggregate_serving is not None
        assert aggregate_frequency is not None
        current_rsrp = stitched_metrics["max_rsrp_dbm"]
        previous_rsrp = aggregate_metrics["max_rsrp_dbm"]
        winning = np.isfinite(current_rsrp) & (
            ~np.isfinite(previous_rsrp) | (current_rsrp > previous_rsrp)
        )
        local_to_aggregate = np.empty(len(frequency_sector_ids), dtype=np.uint32)
        for local_index, sector_id in enumerate(frequency_sector_ids):
            aggregate_index = aggregate_sector_to_index.get(sector_id)
            if aggregate_index is None:
                aggregate_index = len(aggregate_sector_ids)
                aggregate_sector_to_index[sector_id] = aggregate_index
                aggregate_sector_ids.append(sector_id)
            local_to_aggregate[local_index] = aggregate_index
        aggregate_serving[winning] = local_to_aggregate[stitched_serving[winning]]
        aggregate_frequency[winning] = frequency
        for name, values in stitched_metrics.items():
            current_finite = np.isfinite(values)
            aggregate_values = aggregate_metrics[name]
            replace = current_finite & (
                ~np.isfinite(aggregate_values) | (values > aggregate_values)
            )
            aggregate_values[replace] = values[replace]

    aggregate_path = None
    if aggregate_metrics is not None:
        assert aggregate_serving is not None
        assert aggregate_frequency is not None
        aggregate_rsrp = aggregate_metrics["max_rsrp_dbm"]
        aggregate_path = output_dir / "all-bands.npz"
        np.savez_compressed(
            aggregate_path,
            **aggregate_metrics,
            strongest_rsrp_dbm=aggregate_rsrp,
            serving_sector_index=aggregate_serving,
            sector_ids=_sector_table(aggregate_sector_ids),
            frequency_mhz=aggregate_frequency,
            cell_size_m=np.asarray(float(manifest["cell_size_m"])),
            width_m=np.asarray(float(manifest["width_m"])),
            bounds_wgs84=bounds_wgs84,
            anchor_wgs84=np.asarray([settings.anchor[1], settings.anchor[0]]),
            visualization_schema_version=np.asarray(2),
        )

    report = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "ownership": "non-overlapping 1 km tile cores; overlaps used only for seam diagnostics",
        "aggregate": settings.portable_path(aggregate_path) if aggregate_path else None,
        "frequency_groups": reports,
    }
    (output_dir / "stitch-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
