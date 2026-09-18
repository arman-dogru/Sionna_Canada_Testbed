from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ottawa_rt.config import Settings, load_settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.models import ReceiverMeasurement, ServicePrediction
from ottawa_rt.query import PredictionStore


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

    @app.get("/v1/coverage/{run_id}/{tile_name}")
    def coverage_tile(
        run_id: str,
        tile_name: str,
        stride: int = Query(default=4, ge=1, le=50),
        metric: Literal["path_gain", "rss", "rsrp", "sinr"] = "rsrp",
    ) -> dict[str, object]:
        safe_name = Path(tile_name).name
        path = selected.paths.runs / run_id / "tiles" / safe_name
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
                (selected.paths.runs / run_id / "run.json").read_text(encoding="utf-8")
            )
            scene_xml = selected.resolve_path(run["scene_xml"])
            metadata = json.loads(
                (scene_xml.parent / "scene_metadata.json").read_text(encoding="utf-8")
            )
            from pyproj import Transformer

            origin = metadata["local_origin"]
            to_wgs84 = Transformer.from_crs(metadata["target_crs"], "EPSG:4326", always_xy=True)
            corners = [
                to_wgs84.transform(
                    float(origin["easting"]) + center_x + dx,
                    float(origin["northing"]) + center_y + dy,
                )
                for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half))
            ]
            return {
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
                "values": np.nan_to_num(strongest, nan=-200.0, neginf=-200.0, posinf=50.0).tolist(),
                "heights_m": heights.tolist() if heights is not None else None,
            }

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

        @app.get("/nvidia-logo.svg", include_in_schema=False)
        def nvidia_logo() -> FileResponse:
            return FileResponse(web_dist / "nvidia-logo.svg", media_type="image/svg+xml")

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
