from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ottawa_rt.config import Settings
from ottawa_rt.geo import bbox_from_center

CANONICAL_FIELDS = [
    "measurement_id",
    "timestamp",
    "latitude",
    "longitude",
    "height_agl_m",
    "frequency_mhz",
    "operator",
    "cell_id",
    "physical_cell_id",
    "rsrp_dbm",
    "rssi_dbm",
    "rsrq_db",
    "sinr_db",
]

LINEAGE_FIELDS = [
    "measurement_id",
    "source_file",
    "source_row",
    "node_id",
    "cgi",
    "reported_cell_id",
    "rat",
    "band",
    "bandwidth_mhz",
    "power_dbm",
    "altitude_m",
]

RAW_ALIASES = {
    "timestamp": ("recordtimeutc", "recorddate", "timestamp", "time"),
    "node_id": ("nodeid",),
    "cgi": ("cgi",),
    "operator": ("operator", "provider"),
    "frequency_mhz": ("frequencymhz", "frequency"),
    "band": ("band",),
    "bandwidth_mhz": ("bandwidthmhz", "bandwidth"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "physical_cell_id": ("pcipscbsic", "pci", "physicalcellid"),
    "reported_cell_id": ("cellid",),
    "power_dbm": ("powerdbm", "power"),
    "rat": ("rat", "technology"),
    "sinr_db": ("rssinrdb", "rssinr", "sinrdb", "sinr"),
    "rsrp_dbm": ("rsrprscprsschdbm", "rsrprscprssch", "rsrpdbm", "rsrp"),
    "rsrq_db": ("rsrqdb", "rsrq"),
    "rssi_dbm": ("rssidbm", "rssi"),
    "altitude_m": ("altitude",),
}

INVALID_TEXT = {"", "-1", "[-1]-[-1]", "unknown", "none", "null", "nan"}


def _normalized_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _column_map(fieldnames: list[str]) -> dict[str, str | None]:
    normalized = {_normalized_header(field): field for field in fieldnames}
    return {
        canonical: next((normalized[item] for item in aliases if item in normalized), None)
        for canonical, aliases in RAW_ALIASES.items()
    }


def _text(row: dict[str, str], column: str | None) -> str:
    if column is None:
        return ""
    value = str(row.get(column, "")).strip()
    return "" if value.lower() in INVALID_TEXT else value


def _number(row: dict[str, str], column: str | None) -> float | None:
    value = _text(row, column)
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _bounded_number(
    row: dict[str, str], column: str | None, minimum: float, maximum: float
) -> float | None:
    value = _number(row, column)
    return value if value is not None and minimum <= value <= maximum else None


def _identifier(row: dict[str, str], column: str | None) -> str:
    value = _text(row, column)
    if not value:
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    return str(int(number)) if math.isfinite(number) and number.is_integer() else value


def _timestamp(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_number(value: float | None) -> str:
    return "" if value is None else format(value, ".12g")


def build_measurement_snapshot(
    settings: Settings,
    input_dir: Path,
    *,
    run_id: str,
    snapshot_name: str,
) -> dict[str, object]:
    """Create an immutable LTE/NR calibration snapshot for one run footprint."""
    run_path = settings.paths.runs / run_id / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"Run manifest not found: {run_path}")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    bounds = bbox_from_center(
        float(run["anchor_wgs84"]["latitude"]),
        float(run["anchor_wgs84"]["longitude"]),
        float(run["width_m"]),
    )
    source_paths = sorted(path for path in input_dir.glob("*.csv") if path.is_file())
    if not source_paths:
        raise FileNotFoundError(f"No CSV files found in {input_dir}")

    snapshot_dir = settings.paths.measurements / "snapshots" / snapshot_name
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        raise FileExistsError(f"Snapshot already exists and is immutable: {snapshot_dir}")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = snapshot_dir / "receiver-measurements.csv"
    lineage_path = snapshot_dir / "lineage.csv"
    canonical_temporary = canonical_path.with_suffix(".tmp")
    lineage_temporary = lineage_path.with_suffix(".tmp")

    seen: set[str] = set()
    total_counts: Counter[str] = Counter()
    source_reports = []
    with (
        canonical_temporary.open("w", encoding="utf-8", newline="") as canonical_handle,
        lineage_temporary.open("w", encoding="utf-8", newline="") as lineage_handle,
    ):
        canonical_writer = csv.DictWriter(canonical_handle, fieldnames=CANONICAL_FIELDS)
        lineage_writer = csv.DictWriter(lineage_handle, fieldnames=LINEAGE_FIELDS)
        canonical_writer.writeheader()
        lineage_writer.writeheader()

        for source_path in source_paths:
            source_hash = _sha256(source_path)
            counts: Counter[str] = Counter()
            with source_path.open("r", encoding="utf-8-sig", newline="") as source_handle:
                reader = csv.DictReader(source_handle)
                if reader.fieldnames is None:
                    counts["missing_header"] += 1
                    continue
                columns = _column_map(reader.fieldnames)
                required_columns = ("timestamp", "frequency_mhz", "latitude", "longitude", "rsrp_dbm", "rat")
                missing = [key for key in required_columns if columns[key] is None]
                if missing:
                    raise ValueError(
                        f"{source_path.name} is missing required fields: {', '.join(missing)}"
                    )
                for source_row, raw in enumerate(reader, 2):
                    counts["source_rows"] += 1
                    latitude = _bounded_number(raw, columns["latitude"], -90.0, 90.0)
                    longitude = _bounded_number(raw, columns["longitude"], -180.0, 180.0)
                    if latitude is None or longitude is None:
                        counts["invalid_coordinate"] += 1
                        continue
                    if not bounds.contains(latitude, longitude):
                        counts["outside_run_output"] += 1
                        continue
                    timestamp = _timestamp(_text(raw, columns["timestamp"]))
                    if timestamp is None:
                        counts["invalid_timestamp"] += 1
                        continue
                    frequency = _number(raw, columns["frequency_mhz"])
                    if frequency is None or frequency <= 0:
                        counts["invalid_frequency"] += 1
                        continue
                    rsrp = _bounded_number(raw, columns["rsrp_dbm"], -200.0, 20.0)
                    if rsrp is None:
                        counts["invalid_rsrp"] += 1
                        continue
                    rat = _text(raw, columns["rat"]).upper()
                    if rat not in {"LTE", "NR5G", "5G", "NR"}:
                        counts["non_lte_nr"] += 1
                        continue
                    node_id = _text(raw, columns["node_id"])
                    cgi = _text(raw, columns["cgi"])
                    pci = _identifier(raw, columns["physical_cell_id"])
                    signature = "\x1f".join(
                        (
                            timestamp,
                            node_id,
                            format(frequency, ".12g"),
                            format(latitude, ".12g"),
                            format(longitude, ".12g"),
                            cgi,
                            pci,
                            format(rsrp, ".12g"),
                        )
                    )
                    if signature in seen:
                        counts["duplicate"] += 1
                        continue
                    seen.add(signature)
                    measurement_id = "m-" + hashlib.sha256(
                        f"{source_hash}:{source_row}:{signature}".encode()
                    ).hexdigest()[:24]
                    operator = _text(raw, columns["operator"])
                    canonical_writer.writerow(
                        {
                            "measurement_id": measurement_id,
                            "timestamp": timestamp,
                            "latitude": _format_number(latitude),
                            "longitude": _format_number(longitude),
                            "height_agl_m": _format_number(settings.default_receiver_height_m),
                            "frequency_mhz": _format_number(frequency),
                            "operator": operator,
                            "cell_id": "",
                            "physical_cell_id": pci,
                            "rsrp_dbm": _format_number(rsrp),
                            "rssi_dbm": _format_number(
                                _bounded_number(raw, columns["rssi_dbm"], -200.0, 50.0)
                            ),
                            "rsrq_db": _format_number(
                                _bounded_number(raw, columns["rsrq_db"], -60.0, 20.0)
                            ),
                            "sinr_db": _format_number(
                                _bounded_number(raw, columns["sinr_db"], -60.0, 100.0)
                            ),
                        }
                    )
                    lineage_writer.writerow(
                        {
                            "measurement_id": measurement_id,
                            "source_file": source_path.name,
                            "source_row": source_row,
                            "node_id": node_id,
                            "cgi": cgi,
                            "reported_cell_id": _text(raw, columns["reported_cell_id"]),
                            "rat": rat,
                            "band": _text(raw, columns["band"]),
                            "bandwidth_mhz": _format_number(_number(raw, columns["bandwidth_mhz"])),
                            "power_dbm": _format_number(_number(raw, columns["power_dbm"])),
                            "altitude_m": _format_number(_number(raw, columns["altitude_m"])),
                        }
                    )
                    counts["included"] += 1
            total_counts.update(counts)
            source_reports.append(
                {
                    "file": source_path.name,
                    "sha256": source_hash,
                    "counts": dict(sorted(counts.items())),
                }
            )

    canonical_temporary.replace(canonical_path)
    lineage_temporary.replace(lineage_path)
    manifest = {
        "schema_version": 1,
        "snapshot_name": snapshot_name,
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "run_output_bbox_wgs84": bounds.as_list(),
        "selection": {
            "radio_access_technologies": ["LTE", "NR5G", "5G", "NR"],
            "requires_valid_rsrp": True,
            "deduplication_key": [
                "timestamp",
                "node_id",
                "frequency_mhz",
                "latitude",
                "longitude",
                "cgi",
                "physical_cell_id",
                "rsrp_dbm",
            ],
            "receiver_height_agl_m": settings.default_receiver_height_m,
            "cell_identity_policy": (
                "Measured CGI and reported cell ID did not exactly match the ISED sector cell IDs; "
                "canonical cell_id is blank and matching uses operator, frequency, PCI, distance, and bearing."
            ),
        },
        "counts": dict(sorted(total_counts.items())),
        "sources": source_reports,
        "outputs": {
            "canonical_csv": str(canonical_path),
            "canonical_sha256": _sha256(canonical_path),
            "lineage_csv": str(lineage_path),
            "lineage_sha256": _sha256(lineage_path),
        },
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {**manifest, "manifest": str(manifest_path)}
