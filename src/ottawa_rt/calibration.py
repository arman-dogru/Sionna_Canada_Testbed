from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.geo import angular_difference_deg, band_label, bearing_deg, haversine_m
from ottawa_rt.models import ReceiverMeasurement, SectorRecord
from ottawa_rt.query import PredictionStore

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


def build_baseline(
    settings: Settings, measurement_path: Path, *, run_id: str | None = None
) -> Path:
    store = PredictionStore(settings)
    records = []
    for measurement in load_measurements(measurement_path):
        if measurement.match_status != "matched" or measurement.rsrp_dbm is None:
            continue
        result = store.query(
            measurement.latitude,
            measurement.longitude,
            measurement.height_agl_m,
            run_id=run_id,
            frequency_mhz=measurement.frequency_mhz,
            limit=100,
            calibrated=False,
        )
        prediction = next(
            (
                item
                for item in result.predictions
                if item.sector_id == measurement.matched_sector_id
            ),
            None,
        )
        if prediction is None:
            continue
        records.append(
            {
                "measurement_id": measurement.measurement_id,
                "split": measurement.split,
                "sector_id": measurement.matched_sector_id,
                "frequency_mhz": measurement.frequency_mhz,
                "measured_rsrp_dbm": measurement.rsrp_dbm,
                "predicted_rsrp_dbm": prediction.rsrp_dbm,
            }
        )
    output = settings.paths.calibration / f"baseline-{measurement_path.stem}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
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
    model = {
        "schema_version": 1,
        "model_id": datetime.now(UTC).strftime("calibration-%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(UTC).isoformat(),
        "baseline": str(baseline_path),
        "method": "robust staged dB offsets with spatial holdout",
        "global_receiver_offset_db": global_offset,
        "frequency_group_offsets_db": per_band,
        "sector_eirp_offsets_db": per_sector,
        "sector_offset_bounds_db": [-6.0, 6.0],
        "metrics": metrics,
    }
    output = settings.paths.calibration / f"{model['model_id']}.json"
    output.write_text(json.dumps(model, indent=2), encoding="utf-8")
    (settings.paths.calibration / "latest.json").write_text(
        json.dumps(model, indent=2), encoding="utf-8"
    )
    return model
