from __future__ import annotations

import csv
import json
import math
import zipfile
from collections.abc import Iterator
from pathlib import Path

from ottawa_rt.geo import BoundingBox
from ottawa_rt.models import SectorRecord

MOBILE_TECHNOLOGIES = ("LTE", "5G", "NR", "UMTS", "HSPA", "GSM", "CDMA")


def _number(value: str | None, default: float, name: str, defaults: list[str]) -> float:
    try:
        result = float(value) if value not in (None, "") else math.nan
        if not math.isfinite(result):
            raise ValueError
        return result
    except (TypeError, ValueError):
        defaults.append(name)
        return default


def _optional_number(value: str | None) -> float | None:
    try:
        result = float(value) if value not in (None, "") else math.nan
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _bounded_number(
    value: str | None,
    default: float,
    name: str,
    defaults: list[str],
    lower: float,
    upper: float,
) -> float:
    parsed = _number(value, default, name, defaults)
    if lower <= parsed <= upper:
        return parsed
    defaults.append(f"{name}:source_out_of_range={parsed:g}")
    return default


def _is_mobile(row: dict[str, str]) -> bool:
    technology = (row.get("technology") or "").upper()
    return any(token in technology for token in MOBILE_TECHNOLOGIES)


def _sector_from_row(
    row: dict[str, str], snapshot: str, defaults_cfg: dict[str, float]
) -> SectorRecord | None:
    if not _is_mobile(row) or not row.get("tx_frequency"):
        return None
    try:
        latitude, longitude = float(row["latitude"]), float(row["longitude"])
        frequency = float(row["tx_frequency"])
    except (KeyError, TypeError, ValueError):
        return None
    defaults: list[str] = []
    omni = (row.get("tx_ant_omni_indicator") or "").upper() in {"O", "Y", "YES", "1", "TRUE"}
    azimuth = 0.0 if omni else _number(row.get("tx_ant_azimuth"), 0.0, "azimuth_deg", defaults)
    h_bw = (
        360.0
        if omni
        else _number(
            row.get("tx_ant_horiz_beamwidth"),
            defaults_cfg["horizontal_beamwidth_deg"],
            "horizontal_beamwidth_deg",
            defaults,
        )
    )
    record_id = row.get("record_id") or ""
    stable_parts = [
        record_id,
        row.get("licence_number") or "",
        row.get("cell_id") or "",
        f"{frequency:.4f}",
        f"{azimuth:.2f}",
    ]
    sector_id = "|".join(stable_parts)
    return SectorRecord(
        sector_id=sector_id,
        licence_number=row.get("licence_number") or None,
        operator=(row.get("licensee_name") or "Unknown").strip(),
        technology=(row.get("technology") or "Unknown").strip(),
        cell_id=row.get("cell_id") or None,
        physical_cell_id=row.get("physical_id") or None,
        latitude=latitude,
        longitude=longitude,
        tx_frequency_mhz=frequency,
        bandwidth_mhz=_optional_number(row.get("bandwidth")),
        tx_power_dbm=_bounded_number(
            row.get("tx_power"), defaults_cfg["tx_power_dbm"], "tx_power_dbm", defaults, -20.0, 80.0
        ),
        antenna_height_agl_m=_number(
            row.get("tx_ant_height"), defaults_cfg["height_agl_m"], "antenna_height_agl_m", defaults
        ),
        antenna_gain_dbi=_number(
            row.get("tx_ant_gain"), defaults_cfg["gain_dbi"], "antenna_gain_dbi", defaults
        ),
        line_loss_db=_number(
            row.get("tx_line_loss"), defaults_cfg["line_loss_db"], "line_loss_db", defaults
        ),
        azimuth_deg=azimuth % 360.0,
        downtilt_deg=_number(
            row.get("tx_ant_elevation_angle"),
            defaults_cfg["downtilt_deg"],
            "downtilt_deg",
            defaults,
        ),
        horizontal_beamwidth_deg=min(max(h_bw, 0.1), 360.0),
        vertical_beamwidth_deg=min(
            max(
                _number(
                    row.get("tx_ant_vert_beamwidth"),
                    defaults_cfg["vertical_beamwidth_deg"],
                    "vertical_beamwidth_deg",
                    defaults,
                ),
                0.1,
            ),
            180.0,
        ),
        is_omnidirectional=omni,
        source_record_id=record_id or None,
        source_snapshot=snapshot,
        defaults_applied=sorted(set(defaults)),
    )


def iter_sectors(
    zip_path: Path,
    bbox: BoundingBox,
    defaults_cfg: dict[str, float],
) -> Iterator[SectorRecord]:
    seen: set[str] = set()
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"Expected exactly one CSV in {zip_path}, found {csv_names}")
        with archive.open(csv_names[0]) as binary:
            import io

            with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
                for raw_row in csv.DictReader(text):
                    # ISED marks privacy-sensitive or mandatory extract columns
                    # with a trailing asterisk (for example ``licensee_name*``).
                    # Normalize those schema annotations once at the boundary so
                    # the canonical model is insulated from monthly header changes.
                    row = {
                        key.rstrip("*"): value for key, value in raw_row.items() if key is not None
                    }
                    try:
                        latitude, longitude = float(row["latitude"]), float(row["longitude"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not bbox.contains(latitude, longitude):
                        continue
                    sector = _sector_from_row(row, zip_path.name, defaults_cfg)
                    if sector and sector.sector_id not in seen:
                        seen.add(sector.sector_id)
                        yield sector


def normalize_ised(
    zip_path: Path,
    output_path: Path,
    bbox: BoundingBox,
    defaults_cfg: dict[str, float],
) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp")
    count = defaults_count = 0
    operators: set[str] = set()
    frequencies: set[float] = set()
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for sector in iter_sectors(zip_path, bbox, defaults_cfg):
            handle.write(sector.model_dump_json() + "\n")
            count += 1
            defaults_count += bool(sector.defaults_applied)
            operators.add(sector.operator)
            frequencies.add(sector.tx_frequency_mhz)
    temporary.replace(output_path)
    summary = {
        "sector_count": count,
        "sectors_with_defaults": defaults_count,
        "operators": sorted(operators),
        "frequencies_mhz": sorted(frequencies),
        "bbox_wgs84": bbox.as_list(),
        "output": str(output_path),
    }
    output_path.with_suffix(".summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def load_sectors(path: Path) -> list[SectorRecord]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [SectorRecord.model_validate_json(line) for line in handle if line.strip()]
