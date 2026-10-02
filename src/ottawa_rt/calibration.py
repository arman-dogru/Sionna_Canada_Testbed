from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.data.provenance import sha256_file
from ottawa_rt.geo import angular_difference_deg, band_label, bearing_deg, haversine_m
from ottawa_rt.models import ReceiverMeasurement, SectorRecord
from ottawa_rt.simulation import frequency_bucket_mhz
from ottawa_rt.tiling import Tile, make_tiles

COLUMN_ALIASES = {
    "id": "measurement_id",
    "time": "timestamp",
    "lat": "latitude",
    "lon": "longitude",
    "height": "height_agl_m",
    "frequency": "frequency_mhz",
    "pci": "physical_cell_id",
    "rsrp": "rsrp_dbm",
    "rssi": "rssi_dbm",
    "rsrq": "rsrq_db",
    "sinr": "sinr_db",
}


def _canonical_row(row: dict[str, str], index: int, default_height: float) -> dict[str, object]:
    canonical: dict[str, object] = {}
    for key, value in row.items():
        normalized = key.strip().lower().replace(" ", "_")
        canonical[COLUMN_ALIASES.get(normalized, normalized)] = (
            value.strip() if isinstance(value, str) else value
        )
    canonical.setdefault("measurement_id", f"measurement-{index:08d}")
    canonical.setdefault("height_agl_m", default_height)
    canonical.setdefault("match_status", "unmatched")
    for nullable in (
        "operator",
        "cell_id",
        "physical_cell_id",
        "rsrp_dbm",
        "rssi_dbm",
        "rsrq_db",
        "sinr_db",
    ):
        if canonical.get(nullable) == "":
            canonical[nullable] = None
    return canonical


def _spatial_split(measurement: ReceiverMeasurement, block_deg: float = 0.004) -> str:
    block = f"{math.floor(measurement.latitude / block_deg)}:{math.floor(measurement.longitude / block_deg)}"
    bucket = int(hashlib.sha256(block.encode()).hexdigest()[:8], 16) % 10
    if bucket < 6:
        return "train"
    if bucket < 8:
        return "validation"
    return "test"


def _match_score(measurement: ReceiverMeasurement, sector: SectorRecord) -> float | None:
    frequency_delta = abs(measurement.frequency_mhz - sector.tx_frequency_mhz)
    if frequency_delta > max(5.0, (sector.bandwidth_mhz or 10.0) / 2):
        return None
    distance_km = (
        haversine_m(measurement.latitude, measurement.longitude, sector.latitude, sector.longitude)
        / 1000.0
    )
    if distance_km > 30:
        return None
    score = frequency_delta * 2.0 + distance_km
    if measurement.operator:
        score += 0.0 if measurement.operator.lower() in sector.operator.lower() else 100.0
    if measurement.cell_id:
        score += 0.0 if measurement.cell_id == sector.cell_id else 60.0
    if measurement.physical_cell_id:
        score += 0.0 if measurement.physical_cell_id == sector.physical_cell_id else 20.0
    bearing = bearing_deg(
        sector.latitude, sector.longitude, measurement.latitude, measurement.longitude
    )
    if not sector.is_omnidirectional:
        score += angular_difference_deg(bearing, sector.azimuth_deg) / max(
            sector.horizontal_beamwidth_deg, 1.0
        )
    return score


def match_measurement(
    measurement: ReceiverMeasurement, sectors: list[SectorRecord]
) -> ReceiverMeasurement:
    scored = [
        (score, sector)
        for sector in sectors
        if (score := _match_score(measurement, sector)) is not None
    ]
    scored.sort(key=lambda item: item[0])
    if not scored:
        return measurement.model_copy(update={"match_status": "unmatched"})
    if len(scored) > 1 and scored[1][0] - scored[0][0] < 2.0:
        return measurement.model_copy(update={"match_status": "ambiguous"})
    return measurement.model_copy(
        update={"match_status": "matched", "matched_sector_id": scored[0][1].sector_id}
    )


def import_measurements(settings: Settings, csv_path: Path) -> dict[str, object]:
    sectors = load_sectors(settings.paths.processed / "sectors.jsonl")
    output = settings.paths.measurements / f"{csv_path.stem}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = defaultdict(int)
    temporary = output.with_suffix(".tmp")
    with (
        csv_path.open("r", encoding="utf-8-sig", newline="") as source,
        temporary.open("w", encoding="utf-8", newline="\n") as target,
    ):
        for index, row in enumerate(csv.DictReader(source), 1):
            item = ReceiverMeasurement.model_validate(
                _canonical_row(row, index, settings.default_receiver_height_m)
            )
            item = match_measurement(item, sectors)
            item = item.model_copy(update={"split": _spatial_split(item)})
            target.write(item.model_dump_json() + "\n")
            counts[item.match_status] += 1
            counts[item.split or "unknown_split"] += 1
    temporary.replace(output)
    report = {
        "input": str(csv_path),
        "output": str(output),
        "counts": dict(sorted(counts.items())),
        "imported_at": datetime.now(UTC).isoformat(),
    }
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def load_measurements(path: Path) -> list[ReceiverMeasurement]:
    with path.open("r", encoding="utf-8") as handle:
        return [ReceiverMeasurement.model_validate_json(line) for line in handle if line.strip()]


def _owned_tile(tiles: list[Tile], x_m: float, y_m: float) -> Tile | None:
    """Return the deterministic core tile owning a local scene coordinate."""
    matches = [
        tile
        for tile in tiles
        if abs(x_m - tile.center_x_m) <= tile.size_m / 2
        and abs(y_m - tile.center_y_m) <= tile.size_m / 2
    ]
    return min(matches, key=lambda tile: tile.tile_id) if matches else None


def build_baseline(
    settings: Settings, measurement_path: Path, *, run_id: str | None = None
) -> Path:
    run_ids = sorted(path.parent.name for path in settings.paths.runs.glob("*/run.json"))
    selected_run_id = run_id or (run_ids[-1] if run_ids else None)
    if selected_run_id is None:
        raise FileNotFoundError("No simulation run is available for calibration")
    run_dir = settings.paths.runs / selected_run_id
    run_path = run_dir / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"Run manifest not found: {run_path}")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    scene_xml = settings.resolve_path(run["scene_xml"])
    metadata = json.loads((scene_xml.parent / "scene_metadata.json").read_text(encoding="utf-8"))
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Calibration requires the 'geo' dependency group") from exc
    transformer = Transformer.from_crs("EPSG:4326", metadata["target_crs"], always_xy=True)
    origin = metadata["local_origin"]
    tile_size_m = float(settings.raw["area"]["tile_size_m"])
    overlap_m = float(settings.raw["area"]["overlap_m"])
    tiles = make_tiles(float(run["width_m"]), tile_size_m, overlap_m)
    sectors = {
        sector.sector_id: sector
        for sector in load_sectors(settings.paths.processed / "sectors.jsonl")
    }

    grouped: dict[Path, list[tuple[ReceiverMeasurement, Tile, float, float]]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    for measurement in load_measurements(measurement_path):
        if measurement.match_status != "matched" or measurement.rsrp_dbm is None:
            skipped["not_matched_or_missing_rsrp"] += 1
            continue
        sector = sectors.get(measurement.matched_sector_id or "")
        if sector is None:
            skipped["matched_sector_missing"] += 1
            continue
        easting, northing = transformer.transform(measurement.longitude, measurement.latitude)
        x_m = float(easting) - float(origin["easting"])
        y_m = float(northing) - float(origin["northing"])
        tile = _owned_tile(tiles, x_m, y_m)
        if tile is None:
            skipped["outside_run_output"] += 1
            continue
        frequency = frequency_bucket_mhz(sector.tx_frequency_mhz)
        job_id = f"{tile.tile_id}-{frequency:.1f}MHz".replace(".", "p")
        grouped[run_dir / "tiles" / f"{job_id}.npz"].append((measurement, tile, x_m, y_m))

    records = []
    for tile_path, items in grouped.items():
        if not tile_path.exists():
            skipped["tile_result_missing"] += len(items)
            continue
        with np.load(tile_path, allow_pickle=False) as data:
            sector_indexes = {
                sector_id: index for index, sector_id in enumerate(data["sector_ids"].astype(str))
            }
            center_x, center_y = map(float, data["tile_center"])
            tile_size = float(data["tile_size_m"])
            overlap = float(data["overlap_m"])
            cell = float(data["cell_size_m"])
            total = tile_size + 2 * overlap
            rsrp_layers = np.asarray(data["rsrp_dbm"])
            for measurement, _, x_m, y_m in items:
                sector_index = sector_indexes.get(measurement.matched_sector_id or "")
                if sector_index is None:
                    skipped["sector_missing_from_tile"] += 1
                    continue
                column = int((x_m - (center_x - total / 2)) / cell)
                row = int((y_m - (center_y - total / 2)) / cell)
                shape = rsrp_layers.shape
                if not (0 <= row < shape[1] and 0 <= column < shape[2]):
                    skipped["cell_outside_tile"] += 1
                    continue
                predicted_rsrp = float(rsrp_layers[sector_index, row, column])
                if not math.isfinite(predicted_rsrp):
                    skipped["predicted_rsrp_not_finite"] += 1
                    continue
                records.append(
                    {
                        "measurement_id": measurement.measurement_id,
                        "split": measurement.split,
                        "sector_id": measurement.matched_sector_id,
                        "frequency_mhz": measurement.frequency_mhz,
                        "measured_rsrp_dbm": measurement.rsrp_dbm,
                        "predicted_rsrp_dbm": predicted_rsrp,
                    }
                )
    output = (
        settings.paths.calibration / f"baseline-{measurement_path.stem}-{selected_run_id}.jsonl"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    report = {
        "measurement_path": str(measurement_path),
        "run_id": selected_run_id,
        "output": str(output),
        "record_count": len(records),
        "tile_files_loaded": len(grouped),
        "skipped": dict(sorted(skipped.items())),
        "created_at": datetime.now(UTC).isoformat(),
    }
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output


def _shrunken_median(values: list[float], strength: float = 8.0) -> float:
    if not values:
        return 0.0
    raw = float(np.median(values))
    return raw * len(values) / (len(values) + strength)


def _metrics(
    errors: list[float], threshold_truth: list[bool], threshold_pred: list[bool]
) -> dict[str, float | int | None]:
    if not errors:
        return {
            "count": 0,
            "mae_db": None,
            "rmse_db": None,
            "bias_db": None,
            "p90_abs_db": None,
            "threshold_accuracy": None,
        }
    values = np.asarray(errors)
    return {
        "count": len(errors),
        "mae_db": float(np.mean(np.abs(values))),
        "rmse_db": float(np.sqrt(np.mean(values**2))),
        "bias_db": float(np.mean(values)),
        "p90_abs_db": float(np.percentile(np.abs(values), 90)),
        "threshold_accuracy": float(
            np.mean(np.asarray(threshold_truth) == np.asarray(threshold_pred))
        ),
    }


def calibrate(settings: Settings, baseline_path: Path) -> dict[str, object]:
    rows = [
        json.loads(line) for line in baseline_path.read_text(encoding="utf-8").splitlines() if line
    ]
    train = [row for row in rows if row["split"] == "train"]
    if not train:
        raise ValueError("Calibration requires at least one matched training measurement")
    residuals = [row["measured_rsrp_dbm"] - row["predicted_rsrp_dbm"] for row in train]
    global_offset = float(np.median(residuals))

    per_band_values: dict[str, list[float]] = defaultdict(list)
    for row in train:
        residual = row["measured_rsrp_dbm"] - row["predicted_rsrp_dbm"] - global_offset
        per_band_values[band_label(float(row["frequency_mhz"]))].append(residual)
    per_band = {key: _shrunken_median(values) for key, values in per_band_values.items()}

    per_sector_values: dict[str, list[float]] = defaultdict(list)
    for row in train:
        correction = global_offset + per_band.get(band_label(float(row["frequency_mhz"])), 0.0)
        residual = row["measured_rsrp_dbm"] - row["predicted_rsrp_dbm"] - correction
        per_sector_values[row["sector_id"]].append(residual)
    per_sector = {
        key: float(np.clip(_shrunken_median(values), -6.0, 6.0))
        for key, values in per_sector_values.items()
    }

    threshold = float(settings.raw["simulation"]["service_threshold_rsrp_dbm"])
    metrics = {}
    for split in ("train", "validation", "test"):
        errors, truth, predicted = [], [], []
        for row in (item for item in rows if item["split"] == split):
            calibrated = (
                row["predicted_rsrp_dbm"]
                + global_offset
                + per_band.get(band_label(float(row["frequency_mhz"])), 0.0)
                + per_sector.get(row["sector_id"], 0.0)
            )
            errors.append(calibrated - row["measured_rsrp_dbm"])
            truth.append(row["measured_rsrp_dbm"] >= threshold)
            predicted.append(calibrated >= threshold)
        metrics[split] = _metrics(errors, truth, predicted)
    report_path = baseline_path.with_suffix(".report.json")
    baseline_report = (
        json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    )
    model = {
        "schema_version": 1,
        "model_id": datetime.now(UTC).strftime("calibration-%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(UTC).isoformat(),
        "baseline": str(baseline_path),
        "run_id": baseline_report.get("run_id"),
        "model_version": settings.raw["project"]["model_version"],
        "method": "robust staged dB offsets with spatial holdout",
        "global_receiver_offset_db": global_offset,
        "frequency_group_offsets_db": per_band,
        "sector_eirp_offsets_db": per_sector,
        "sector_offset_bounds_db": [-6.0, 6.0],
        "metrics": metrics,
        "training_frequency_group_counts": dict(
            Counter(band_label(float(row["frequency_mhz"])) for row in train)
        ),
        "training_sector_counts": dict(Counter(row["sector_id"] for row in train)),
        "baseline_sha256": sha256_file(baseline_path),
    }
    measurement_value = baseline_report.get("measurement_path")
    if measurement_value:
        measurement_file = settings.resolve_path(str(measurement_value))
        if measurement_file.exists():
            model["measurement_provenance"] = {
                "path": settings.portable_path(measurement_file),
                "sha256": sha256_file(measurement_file),
            }
    output = settings.paths.calibration / f"{model['model_id']}.json"
    output.write_text(json.dumps(model, indent=2), encoding="utf-8")
    (settings.paths.calibration / "latest.json").write_text(
        json.dumps(model, indent=2), encoding="utf-8"
    )
    return model
