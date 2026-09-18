from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from ottawa_rt.config import Settings, load_settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.models import ReceiverMeasurement, ServicePrediction
from ottawa_rt.query import PredictionStore

COVERAGE_RENDER_VERSION = 2
COVERAGE_RANGES = {
    "path_gain": (-190.0, -70.0),
    "rss": (-145.0, -35.0),
    "rsrp": (-145.0, -45.0),
    "sinr": (-20.0, 70.0),
}
COVERAGE_COLORS = np.asarray(
    [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]],
    dtype=np.float32,
)


def _write_coverage_png(path: Path, values: np.ndarray, metric: str) -> None:
    """Atomically render a browser-ready coverage raster using the GUI palette."""
    minimum, maximum = COVERAGE_RANGES[metric]
    valid = np.isfinite(values) & (values > -199.0)
    normalized = np.clip((values - minimum) / (maximum - minimum), 0.0, 1.0)
    normalized = np.where(valid, normalized, 0.0)
    scaled = normalized * (len(COVERAGE_COLORS) - 1)
    lower = np.floor(scaled).astype(np.intp)
    upper = np.minimum(lower + 1, len(COVERAGE_COLORS) - 1)
    blend = (scaled - lower)[..., np.newaxis]
    rgb = np.floor(
        COVERAGE_COLORS[lower] + (COVERAGE_COLORS[upper] - COVERAGE_COLORS[lower]) * blend
        + 0.5
    ).astype(np.uint8)
    # Weak finite predictions should not black out the photogrammetry. Increase
    # opacity with signal strength while keeping no-data pixels fully clear.
    alpha = np.where(valid, np.floor(64.0 + normalized * 156.0 + 0.5), 0.0)
    alpha = alpha.astype(np.uint8)[..., np.newaxis]
    rgba = np.concatenate((rgb, alpha), axis=2)
    # The simulation grid is south-up; browser images are north-up.
    rgba = np.flipud(rgba)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}-{uuid4().hex}.tmp")
    try:
        Image.fromarray(rgba, mode="RGBA").save(temporary, format="PNG", compress_level=6)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    import os

    return load_settings(os.getenv("OTTAWA_RT_CONFIG", "config/default.yaml"))


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or get_settings()
    app = FastAPI(
        title="Ottawa Sionna RT",
        version="0.1.0",
        description="Outdoor cellular digital-twin API. Predictions are planning estimates, not service guarantees.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    store = PredictionStore(selected)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "model_version": selected.raw["project"]["model_version"]}

    @app.get("/v1/project")
    def project() -> dict[str, object]:
        return {
            "name": selected.raw["project"]["name"],
            "model_version": selected.raw["project"]["model_version"],
            "anchor": selected.raw["anchor"],
            "area": selected.raw["area"],
            "receiver": selected.raw["receiver"],
            "disclaimer": "Comparative outdoor RF planning estimate; not a commercial-service guarantee.",
        }

    @app.get("/v1/query", response_model=ServicePrediction)
    def query_service(
        latitude: float = Query(ge=-90, le=90),
        longitude: float = Query(ge=-180, le=180),
        height_agl_m: float | None = Query(default=None, ge=-5, le=200),
        run_id: str | None = None,
        operator: str | None = None,
        frequency_mhz: float | None = Query(default=None, gt=0),
        limit: int = Query(default=10, ge=1, le=100),
        calibrated: bool = True,
    ) -> ServicePrediction:
        return store.query(
            latitude,
            longitude,
            height_agl_m,
            run_id=run_id,
            operator=operator,
            frequency_mhz=frequency_mhz,
            limit=limit,
            calibrated=calibrated,
        )

    @app.get("/v1/stations")
    def stations(
        south: float | None = None,
        west: float | None = None,
        north: float | None = None,
        east: float | None = None,
        operator: str | None = None,
        limit: int = Query(default=5000, ge=1, le=25000),
    ) -> dict[str, object]:
        records = load_sectors(selected.paths.processed / "sectors.jsonl")
        features = []
        for record in records:
            if operator and operator.lower() not in record.operator.lower():
                continue
            if None not in (south, west, north, east) and not (
                float(south) <= record.latitude <= float(north)
                and float(west) <= record.longitude <= float(east)
            ):
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [record.longitude, record.latitude],
                    },
                    "properties": {
                        "sector_id": record.sector_id,
                        "operator": record.operator,
                        "technology": record.technology,
                        "frequency_mhz": record.tx_frequency_mhz,
                        "azimuth_deg": record.azimuth_deg,
                        "eirp_dbm": record.eirp_dbm,
                        "confidence": "high" if not record.defaults_applied else "medium",
                    },
                }
            )
            if len(features) >= limit:
                break
        return {"type": "FeatureCollection", "features": features}

    @app.get("/v1/runs")
    def runs() -> list[dict[str, object]]:
        result = []
        for path in sorted(selected.paths.runs.glob("*/run.json"), reverse=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["coverage_tiles"] = sorted(
                item.name for item in (path.parent / "tiles").glob("*.npz")
            )
            payload["stitched_coverage"] = sorted(
                item.name for item in (path.parent / "stitched").glob("*MHz.npz")
            )
            payload["has_combined_coverage"] = (
                path.parent / "stitched" / "all-bands.npz"
            ).exists()
            result.append(payload)
        return result

    @app.get("/v1/provenance")
    def provenance() -> dict[str, object]:
        path = selected.paths.raw / "provenance.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": []}

    @app.get("/v1/calibration")
    def calibration() -> dict[str, object]:
        path = selected.paths.calibration / "latest.json"
        return (
            json.loads(path.read_text(encoding="utf-8"))
            if path.exists()
            else {"status": "not_calibrated"}
        )

    @app.get("/v1/measurements")
    def measurements(limit: int = Query(default=5000, ge=1, le=50000)) -> dict[str, object]:
        candidates = sorted(
            selected.paths.measurements.glob("*.jsonl"), key=lambda item: item.stat().st_mtime
        )
        if not candidates:
            return {"type": "FeatureCollection", "features": [], "source": None}
        source = candidates[-1]
        features = []
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = ReceiverMeasurement.model_validate_json(line)
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [item.longitude, item.latitude],
                        },
                        "properties": {
                            "measurement_id": item.measurement_id,
                            "timestamp": item.timestamp.isoformat(),
                            "height_agl_m": item.height_agl_m,
                            "frequency_mhz": item.frequency_mhz,
                            "operator": item.operator,
                            "cell_id": item.cell_id,
                            "physical_cell_id": item.physical_cell_id,
                            "rsrp_dbm": item.rsrp_dbm,
                            "rssi_dbm": item.rssi_dbm,
                            "rsrq_db": item.rsrq_db,
                            "sinr_db": item.sinr_db,
                            "matched_sector_id": item.matched_sector_id,
                            "match_status": item.match_status,
                            "split": item.split,
                        },
                    }
                )
                if len(features) >= limit:
                    break
        return {
            "type": "FeatureCollection",
            "features": features,
            "source": selected.portable_path(source),
        }

    @app.get("/v1/coverage/{run_id}/{tile_name}", response_model=None)
    def coverage_tile(
        run_id: str,
        tile_name: str,
        stride: int = Query(default=4, ge=1, le=50),
        metric: Literal["path_gain", "rss", "rsrp", "sinr"] = "rsrp",
        format: Literal["json", "metadata", "png"] = "json",
        source: Literal["tiles", "stitched"] = "tiles",
    ) -> dict[str, object] | FileResponse | JSONResponse:
        safe_run_id = Path(run_id).name
        if safe_run_id != run_id:
            raise HTTPException(status_code=404, detail="Coverage run not found")
        safe_name = Path(tile_name).name
        combined = safe_name == "all-bands.npz"
        stitched = combined or source == "stitched"
        layer_dir = "stitched" if stitched else "tiles"
        path = selected.paths.runs / safe_run_id / layer_dir / safe_name
        if path.suffix != ".npz" or not path.exists():
            raise HTTPException(status_code=404, detail="Coverage tile not found")
        metric_fields = {
            "path_gain": ("max_path_gain_db", "path_gain_db", "dB"),
            "rss": ("max_rss_dbm", "rss_dbm", "dBm"),
            "rsrp": ("max_rsrp_dbm", "rsrp_dbm", "dBm"),
            "sinr": ("max_sinr_db", "sinr_db", "dB"),
        }
        maximum_field, source_field, unit = metric_fields[metric]
        with np.load(path, allow_pickle=False) as data:
            if maximum_field in data:
                strongest = np.asarray(data[maximum_field], dtype=np.float32)
            else:
                source = np.asarray(data[source_field])
                strongest = np.max(np.where(np.isfinite(source), source, -np.inf), axis=0)
            if stitched:
                strongest = strongest[::stride, ::stride]
                bounds = np.asarray(data["bounds_wgs84"], dtype=np.float64).tolist()
                cell = float(data["cell_size_m"])
                payload: dict[str, object] = {
                    "tile": safe_name,
                    "tile_center": [0.0, 0.0],
                    "tile_size_m": float(data["width_m"]),
                    "overlap_m": 0.0,
                    "cell_size_m": cell * stride,
                    "bounds_wgs84": bounds,
                    "frequency_group_mhz": (
                        None if combined else float(np.asarray(data["frequency_mhz"]))
                    ),
                    "metric": metric,
                    "field": maximum_field,
                    "unit": unit,
                    "no_data_value": -200.0,
                    "valid_cell_count": int(np.isfinite(strongest).sum()),
                }
                heights = None
            else:
                heights = (
                    np.asarray(data["receiver_z_m"], dtype=np.float32)
                    if "receiver_z_m" in data
                    else None
                )
                cell = float(data["cell_size_m"])
                overlap = float(data["overlap_m"])
                crop = round(overlap / cell)
                if crop:
                    strongest = strongest[crop:-crop, crop:-crop]
                    if heights is not None:
                        heights = heights[crop:-crop, crop:-crop]
                strongest = strongest[::stride, ::stride]
                if heights is not None:
                    heights = heights[::stride, ::stride]
                center_x, center_y = map(float, data["tile_center"])
                half = float(data["tile_size_m"]) / 2.0
                run = json.loads(
                    (selected.paths.runs / safe_run_id / "run.json").read_text(encoding="utf-8")
                )
                scene_xml = selected.resolve_path(run["scene_xml"])
                metadata = json.loads(
                    (scene_xml.parent / "scene_metadata.json").read_text(encoding="utf-8")
                )
                from pyproj import Transformer

                origin = metadata["local_origin"]
                to_wgs84 = Transformer.from_crs(
                    metadata["target_crs"], "EPSG:4326", always_xy=True
                )
                corners = [
                    to_wgs84.transform(
                        float(origin["easting"]) + center_x + dx,
                        float(origin["northing"]) + center_y + dy,
                    )
                    for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half))
                ]
                payload = {
                    "tile": safe_name,
                    "tile_center": data["tile_center"].tolist(),
                    "tile_size_m": float(data["tile_size_m"]),
                    "overlap_m": float(data["overlap_m"]),
                    "cell_size_m": cell * stride,
                    "bounds_wgs84": [
                        min(point[0] for point in corners),
                        min(point[1] for point in corners),
                        max(point[0] for point in corners),
                        max(point[1] for point in corners),
                    ],
                    "frequency_group_mhz": float(
                        data["trace_frequency_mhz"]
                        if "trace_frequency_mhz" in data
                        else np.median(data["frequencies_mhz"])
                    ),
                    "metric": metric,
                    "field": maximum_field,
                    "unit": unit,
                    "no_data_value": -200.0,
                    "valid_cell_count": int(np.isfinite(strongest).sum()),
                }

        stat = path.stat()
        version = f"{stat.st_mtime_ns:x}-{stat.st_size:x}-r{COVERAGE_RENDER_VERSION}"
        image_url = (
            f"/v1/coverage/{quote(safe_run_id, safe='')}/{quote(safe_name, safe='')}"
            f"?stride={stride}&metric={metric}&format=png&source={layer_dir}&v={version}"
        )
        payload.update(
            {
                "artifact_version": version,
                "image_url": image_url,
                "pixel_width": int(strongest.shape[1]),
                "pixel_height": int(strongest.shape[0]),
                "heights_m": None,
            }
        )
        if format == "png":
            cache_path = (
                selected.paths.runs
                / safe_run_id
                / "cache"
                / "coverage"
                / f"{layer_dir}-{path.stem}-{metric}-s{stride}-{version}.png"
            )
            if not cache_path.exists():
                _write_coverage_png(cache_path, strongest, metric)
            return FileResponse(
                cache_path,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        if format == "metadata":
            return JSONResponse(
                payload,
                headers={"Cache-Control": "public, max-age=60, stale-while-revalidate=300"},
            )
        payload["values"] = np.nan_to_num(
            strongest, nan=-200.0, neginf=-200.0, posinf=50.0
        ).tolist()
        payload["heights_m"] = heights.tolist() if heights is not None else None
        return payload

    @app.get("/v1/scenes/{scene_name}/buildings")
    def scene_buildings(scene_name: str) -> FileResponse:
        safe_name = Path(scene_name).name
        path = selected.paths.scenes / safe_name / "buildings.geojson"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Scene building preview not found")
        return FileResponse(path, media_type="application/geo+json")

    web_dist = selected.paths.root / "web" / "dist"
    if web_dist.exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")
        cesium_static = web_dist / "cesiumStatic"
        if cesium_static.exists():
            app.mount("/cesiumStatic", StaticFiles(directory=cesium_static), name="cesium-static")

        @app.get("/whiteLOGO.svg", include_in_schema=False)
        def white_logo() -> FileResponse:
            return FileResponse(web_dist / "whiteLOGO.svg", media_type="image/svg+xml")

        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(web_dist / "favicon.svg", media_type="image/svg+xml")

        @app.get("/cell-tower.svg", include_in_schema=False)
        def cell_tower_icon() -> FileResponse:
            return FileResponse(web_dist / "cell-tower.svg", media_type="image/svg+xml")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(web_dist / "index.html")

    return app


app = create_app()
