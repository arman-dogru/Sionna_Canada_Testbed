from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock, Thread
from uuid import uuid4

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.geo import band_label, thermal_noise_dbm

CALIBRATED_COVERAGE_VERSION = 1
_build_lock = RLock()
_output_locks: dict[Path, RLock] = {}


def offset_db(model: dict[str, object], sector_id: str, frequency_mhz: float) -> float:
    return (
        float(model.get("global_receiver_offset_db", 0.0))
        + float(model.get("frequency_group_offsets_db", {}).get(band_label(frequency_mhz), 0.0))
        + float(model.get("sector_eirp_offsets_db", {}).get(sector_id, 0.0))
    )


def calibration_for_run(settings: Settings, run_id: str) -> dict[str, object] | None:
    """Use a fitted model only with the same scientific model and scene."""
    path = settings.paths.calibration / "latest.json"
    if not path.exists():
        return None
    model = json.loads(path.read_text(encoding="utf-8"))
    source_run_id = model.get("run_id")
    if not source_run_id and model.get("baseline"):
        baseline = settings.resolve_path(str(model["baseline"]))
        report_path = baseline.with_suffix(".report.json")
        if report_path.exists():
            source_run_id = json.loads(report_path.read_text(encoding="utf-8")).get("run_id")
    target_path = settings.paths.runs / run_id / "run.json"
    if not target_path.exists():
        return None
    target = json.loads(target_path.read_text(encoding="utf-8"))
    if model.get("model_version") and model["model_version"] != target.get("model_version"):
        return None
    if source_run_id:
        source_path = settings.paths.runs / str(source_run_id) / "run.json"
        if not source_path.exists():
            return None
        source = json.loads(source_path.read_text(encoding="utf-8"))
        if any(source.get(key) != target.get(key) for key in ("model_version", "scene_xml")):
            return None
    return model


def sector_bandwidths(settings: Settings) -> dict[str, float | None]:
    return {
        item.sector_id: item.bandwidth_mhz
        for item in load_sectors(settings.paths.processed / "sectors.jsonl")
    }


def calibrated_layers(
    data: object,
    model: dict[str, object],
    noise_figure_db: float,
    bandwidths: dict[str, float | None],
) -> dict[str, np.ndarray]:
    """Correct every sector before finding maxima; recompute SINR in linear power."""
    sectors = np.asarray(data["sector_ids"]).astype(str)
    frequencies = np.asarray(data["frequencies_mhz"], dtype=np.float64)
    corrections = np.asarray(
        [offset_db(model, sector_id, float(freq)) for sector_id, freq in zip(sectors, frequencies)],
        dtype=np.float32,
    )[:, None, None]
    raw_rss = np.asarray(data["rss_dbm"], dtype=np.float32)
    raw_rsrp = np.asarray(data["rsrp_dbm"], dtype=np.float32)
    rss = np.where(np.isfinite(raw_rss), raw_rss + corrections, -np.inf)
    rsrp = np.where(np.isfinite(raw_rsrp), raw_rsrp + corrections, -np.inf)
    if "bandwidths_mhz" in data:
        widths = np.asarray(data["bandwidths_mhz"], dtype=np.float64)
    else:
        known = [bandwidths.get(sector_id) or 20.0 for sector_id in sectors]
        representative = float(np.median(known))
        widths = np.asarray([bandwidths.get(sector_id) or representative for sector_id in sectors])
    signal = np.power(10.0, rss.astype(np.float64) / 10.0)
    total = signal.sum(axis=0)
    noise = np.asarray(
        [10.0 ** (thermal_noise_dbm(float(w), noise_figure_db) / 10.0) for w in widths]
    )
    denominator = np.maximum(
        total[None, :, :] - signal + noise[:, None, None], np.finfo(float).tiny
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        sinr = (10.0 * np.log10(signal / denominator)).astype(np.float32)
    return {"rss_dbm": rss, "rsrp_dbm": rsrp, "sinr_db": sinr, "sector_ids": sectors}


def atomic_save_npz(path: Path, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}-{uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **values)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def coverage_version(
    settings: Settings, run_id: str, model: dict[str, object] | None = None
) -> str:
    run_dir = settings.paths.runs / run_id
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    # Finalization changes the source rasters. Include their versions as well as
    # model contents, so a refit with the same model_id cannot reuse stale images.
    sources = []
    for path in sorted((run_dir / "stitched").glob("*.npz")):
        stat = path.stat()
        sources.append((path.name, stat.st_mtime_ns, stat.st_size))
    tile_dir = run_dir / "tiles"
    sector_path = settings.paths.processed / "sectors.jsonl"
    sector_stat = sector_path.stat() if sector_path.exists() else None
    payload = {
        "schema": CALIBRATED_COVERAGE_VERSION,
        "model": model,
        "run": manifest,
        "sources": sources,
        "tiles_modified": tile_dir.stat().st_mtime_ns if tile_dir.exists() else None,
        "sectors": (sector_stat.st_mtime_ns, sector_stat.st_size) if sector_stat else None,
        "noise_figure_db": settings.raw["receiver"]["noise_figure_db"],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def coverage_directory(settings: Settings, run_id: str, model: dict[str, object]) -> Path:
    return (
        settings.paths.runs
        / run_id
        / "cache"
        / "calibrated"
        / coverage_version(settings, run_id, model)
    )


def ensure_calibrated_coverage(
    settings: Settings, run_id: str, model: dict[str, object], *, progress=None
) -> Path:
    """Persist native-resolution per-band and combined maps without changing raw tiles."""
    from ottawa_rt.stitch import stitch_run

    output = coverage_directory(settings, run_id, model)
    if calibrated_coverage_ready(output):
        return output
    with _build_lock:
        output_lock = _output_locks.setdefault(output, RLock())
    with output_lock:
        if calibrated_coverage_ready(output):
            return output
        stitch_run(settings, run_id, output_dir=output, calibration_model=model, progress=progress)
    return output


def calibrated_coverage_ready(output: Path) -> bool:
    report_path = output / "stitch-report.json"
    if not report_path.exists():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(
        report.get("aggregate")
        and (output / "all-bands.npz").exists()
        and all(
            (output / Path(item["output"]).name).exists()
            for item in report["frequency_groups"]
            if item.get("status") == "complete"
        )
    )


class CoverageBuilds:
    """Coalesce GUI requests into one build and expose its progress without blocking HTTP."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._jobs: dict[Path, dict[str, object]] = {}

    def status(
        self, settings: Settings, run_id: str, model: dict[str, object]
    ) -> dict[str, object] | None:
        output = coverage_directory(settings, run_id, model)
        if calibrated_coverage_ready(output):
            return None
        with self._lock:
            if output not in self._jobs:
                total = len(list((settings.paths.runs / run_id / "stitched").glob("*MHz.npz")))
                self._jobs[output] = {
                    "status": "building",
                    "completed_bands": 0,
                    "total_bands": total,
                }
                Thread(
                    target=self._build, args=(settings, run_id, model, output), daemon=True
                ).start()
            result = dict(self._jobs[output])
            # A later request can retry a failed build rather than caching the failure forever.
            if result["status"] == "failed":
                del self._jobs[output]
            return result

    def _build(
        self, settings: Settings, run_id: str, model: dict[str, object], output: Path
    ) -> None:
        def progress(frequency: float, completed: int, total: int) -> None:
            with self._lock:
                self._jobs[output].update(
                    completed_bands=completed, total_bands=total, frequency_mhz=frequency
                )

        try:
            ensure_calibrated_coverage(settings, run_id, model, progress=progress)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("Calibrated coverage build failed for %s", run_id)
            with self._lock:
                self._jobs[output].update(status="failed", detail=str(exc))
