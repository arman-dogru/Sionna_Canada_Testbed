from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.geo import band_label, free_space_path_loss_db, haversine_m, thermal_noise_dbm
from ottawa_rt.models import SectorPrediction, ServicePrediction
from ottawa_rt.simulation import _local_xy, resource_blocks


def _latest_run(settings: Settings) -> str | None:
    runs = sorted(path.parent.name for path in settings.paths.runs.glob("*/run.json"))
    return runs[-1] if runs else None


def _confidence(defaults: list[str]) -> str:
    if not defaults:
        return "high"
    return "medium" if len(defaults) <= 2 else "low"


def calibration_offset_db(model: dict[str, object], prediction: SectorPrediction) -> float:
    band_offsets = model.get("frequency_group_offsets_db", {})
    sector_offsets = model.get("sector_eirp_offsets_db", {})
    return (
        float(model.get("global_receiver_offset_db", 0.0))
        + float(band_offsets.get(band_label(prediction.frequency_mhz), 0.0))
        + float(sector_offsets.get(prediction.sector_id, 0.0))
    )


class PredictionStore:
    def __init__(self, settings: Settings):
        self.settings = settings

    def query(
        self,
        latitude: float,
        longitude: float,
        height_agl_m: float | None = None,
        *,
        run_id: str | None = None,
        operator: str | None = None,
        frequency_mhz: float | None = None,
        limit: int = 10,
        calibrated: bool = True,
    ) -> ServicePrediction:
        run_id = run_id or _latest_run(self.settings) or "free-space-preview"
        height = (
            height_agl_m if height_agl_m is not None else self.settings.default_receiver_height_m
        )
        if run_id != "free-space-preview":
            prediction = self._query_cached(
                run_id,
                latitude,
                longitude,
                height,
                operator=operator,
                frequency_mhz=frequency_mhz,
                limit=limit,
            )
            if prediction is not None:
                return self._apply_calibration(prediction) if calibrated else prediction
        prediction = self._query_free_space(
            latitude, longitude, height, operator=operator, frequency_mhz=frequency_mhz, limit=limit
        )
        return self._apply_calibration(prediction) if calibrated else prediction

    def _apply_calibration(self, result: ServicePrediction) -> ServicePrediction:
        calibration_path = self.settings.paths.calibration / "latest.json"
        if not calibration_path.exists():
            return result
        model = json.loads(calibration_path.read_text(encoding="utf-8"))
        calibrated = []
        for prediction in result.predictions:
            offset = calibration_offset_db(model, prediction)
            calibrated.append(
                prediction.model_copy(
                    update={
                        "received_power_dbm": prediction.received_power_dbm + offset,
                        "rsrp_dbm": prediction.rsrp_dbm + offset,
                        "rssi_dbm": prediction.rssi_dbm + offset,
                        "sinr_db": prediction.sinr_db + offset,
                    }
                )
            )
        calibrated.sort(key=lambda item: item.rsrp_dbm, reverse=True)
        ranked = [item.model_copy(update={"rank": rank}) for rank, item in enumerate(calibrated, 1)]
        threshold = float(self.settings.raw["simulation"]["service_threshold_rsrp_dbm"])
        model_id = str(model.get("model_id", "calibration-unknown"))
        return result.model_copy(
            update={
                "model_version": f"{result.model_version}+{model_id}",
                "serving_sector_id": ranked[0].sector_id if ranked else None,
                "service_available": bool(ranked and ranked[0].rsrp_dbm >= threshold),
                "predictions": ranked,
                "warnings": [*result.warnings, f"Applied calibration model {model_id}."],
            }
        )

    def _query_cached(
        self,
        run_id: str,
        latitude: float,
        longitude: float,
        height: float,
        *,
        operator: str | None,
        frequency_mhz: float | None,
        limit: int,
    ) -> ServicePrediction | None:
        run_dir = self.settings.paths.runs / run_id
        manifest_path = run_dir / "run.json"
        if not manifest_path.exists():
            return None
        run = json.loads(manifest_path.read_text(encoding="utf-8"))
        scene_xml = self.settings.resolve_path(run["scene_xml"])
        metadata = json.loads(
            (scene_xml.parent / "scene_metadata.json").read_text(encoding="utf-8")
        )
        x, y = _local_xy(metadata, latitude, longitude)
        candidates: list[SectorPrediction] = []
        for path in (run_dir / "tiles").glob("*.npz"):
            with np.load(path, allow_pickle=False) as data:
                center_x, center_y = map(float, data["tile_center"])
                tile_size = float(data["tile_size_m"])
                if not (abs(x - center_x) <= tile_size / 2 and abs(y - center_y) <= tile_size / 2):
                    continue
                cell = float(data["cell_size_m"])
                overlap = float(data["overlap_m"])
                total = tile_size + 2 * overlap
                col = int((x - (center_x - total / 2)) / cell)
                row = int((y - (center_y - total / 2)) / cell)
                shape = data["rsrp_dbm"].shape
                if not (0 <= row < shape[1] and 0 <= col < shape[2]):
                    continue
                for index, sector_id in enumerate(data["sector_ids"].astype(str)):
                    item_operator = str(data["operators"][index])
                    item_frequency = float(data["frequencies_mhz"][index])
                    if operator and operator.lower() not in item_operator.lower():
                        continue
                    if frequency_mhz and abs(item_frequency - frequency_mhz) > 5:
                        continue
                    defaults = json.loads(str(data["defaults_applied"][index]))
                    rsrp = float(data["rsrp_dbm"][index, row, col])
                    if not math.isfinite(rsrp):
                        continue
                    candidates.append(
                        SectorPrediction(
                            sector_id=sector_id,
                            operator=item_operator,
                            technology=str(data["technologies"][index]),
                            frequency_mhz=item_frequency,
                            path_gain_db=float(data["path_gain_db"][index, row, col]),
                            received_power_dbm=float(data["rss_dbm"][index, row, col]),
                            rsrp_dbm=rsrp,
                            rssi_dbm=float(data["rss_dbm"][index, row, col]),
                            sinr_db=float(data["sinr_db"][index, row, col]),
                            rank=0,
                            confidence=_confidence(defaults),
                            defaults_applied=defaults,
                        )
                    )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.rsrp_dbm, reverse=True)
        ranked = [
            item.model_copy(update={"rank": rank})
            for rank, item in enumerate(candidates[:limit], 1)
        ]
        threshold = float(self.settings.raw["simulation"]["service_threshold_rsrp_dbm"])
        return ServicePrediction(
            latitude=latitude,
            longitude=longitude,
            height_agl_m=height,
            model_version=str(run["model_version"]),
            run_id=run_id,
            serving_sector_id=ranked[0].sector_id if ranked else None,
            service_available=bool(ranked and ranked[0].rsrp_dbm >= threshold),
            predictions=ranked,
            generated_at=datetime.now(UTC),
            warnings=[],
        )

    def _query_free_space(
        self,
        latitude: float,
        longitude: float,
        height: float,
        *,
        operator: str | None,
        frequency_mhz: float | None,
        limit: int,
    ) -> ServicePrediction:
        sectors = load_sectors(self.settings.paths.processed / "sectors.jsonl")
        candidates: list[SectorPrediction] = []
        for sector in sectors:
            if operator and operator.lower() not in sector.operator.lower():
                continue
            if frequency_mhz and abs(sector.tx_frequency_mhz - frequency_mhz) > 5:
                continue
            distance = haversine_m(latitude, longitude, sector.latitude, sector.longitude)
            path_gain = -free_space_path_loss_db(distance, sector.tx_frequency_mhz)
            rss = sector.eirp_dbm + path_gain
            rsrp = rss - 10 * math.log10(resource_blocks(sector.bandwidth_mhz) * 12)
            noise = thermal_noise_dbm(
                sector.bandwidth_mhz or 20.0,
                float(self.settings.raw["receiver"]["noise_figure_db"]),
            )
            candidates.append(
                SectorPrediction(
                    sector_id=sector.sector_id,
                    operator=sector.operator,
                    technology=sector.technology,
                    frequency_mhz=sector.tx_frequency_mhz,
                    path_gain_db=path_gain,
                    received_power_dbm=rss,
                    rsrp_dbm=rsrp,
                    rssi_dbm=rss,
                    sinr_db=rss - noise,
                    rank=0,
                    confidence="low",
                    defaults_applied=sector.defaults_applied,
                )
            )
        candidates.sort(key=lambda item: item.rsrp_dbm, reverse=True)
        ranked = [
            item.model_copy(update={"rank": rank})
            for rank, item in enumerate(candidates[:limit], 1)
        ]
        threshold = float(self.settings.raw["simulation"]["service_threshold_rsrp_dbm"])
        return ServicePrediction(
            latitude=latitude,
            longitude=longitude,
            height_agl_m=height,
            model_version="free-space-preview",
            run_id="free-space-preview",
            serving_sector_id=ranked[0].sector_id if ranked else None,
            service_available=bool(ranked and ranked[0].rsrp_dbm >= threshold),
            predictions=ranked,
            generated_at=datetime.now(UTC),
            warnings=[
                "No cached Sionna tile covered this point; values are free-space preview estimates."
            ],
        )
