from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import numpy as np

from ottawa_rt.calibrated_coverage import (
    calibrated_layers,
    calibration_for_run,
    offset_db,
    sector_bandwidths,
)
from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.geo import free_space_path_loss_db, haversine_m, thermal_noise_dbm
from ottawa_rt.models import SectorPrediction, ServicePrediction
from ottawa_rt.simulation import _local_xy, frequency_bucket_mhz, resource_blocks
from ottawa_rt.tiling import make_tiles


def _latest_run(settings: Settings) -> str | None:
    runs = sorted(path.parent.name for path in settings.paths.runs.glob("*/run.json"))
    return runs[-1] if runs else None


def _confidence(defaults: list[str]) -> str:
    if not defaults:
        return "high"
    return "medium" if len(defaults) <= 2 else "low"


def calibration_offset_db(model: dict[str, object], prediction: SectorPrediction) -> float:
    return offset_db(model, prediction.sector_id, prediction.frequency_mhz)


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
        model = calibration_for_run(self.settings, run_id) if calibrated else None
        if run_id != "free-space-preview":
            prediction = self._query_cached(
                run_id,
                latitude,
                longitude,
                height,
                operator=operator,
                frequency_mhz=frequency_mhz,
                limit=limit,
                calibration_model=model,
            )
            if prediction is not None:
                return prediction
        return self._query_free_space(
            latitude,
            longitude,
            height,
            operator=operator,
            frequency_mhz=frequency_mhz,
            limit=limit,
            calibration_model=model,
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
        calibration_model: dict[str, object] | None = None,
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
        bandwidths = sector_bandwidths(self.settings) if calibration_model is not None else {}
        first_path = next((run_dir / "tiles").glob("*.npz"), None)
        if first_path is None:
            return None
        with np.load(first_path, allow_pickle=False) as first:
            tile_size = float(first["tile_size_m"])
            overlap = float(first["overlap_m"])
        owners = [
            tile
            for tile in make_tiles(float(run["width_m"]), tile_size, overlap)
            if abs(x - tile.center_x_m) <= tile.size_m / 2
            and abs(y - tile.center_y_m) <= tile.size_m / 2
        ]
        if not owners:
            return None
        owner = min(owners, key=lambda tile: tile.tile_id)
        # Only decompress the owning tile's bands, rather than opening every
        # tile in the full run for each clicked coordinate.
        pattern = f"{owner.tile_id}-*.npz"
        if frequency_mhz is not None:
            pattern = (
                f"{owner.tile_id}-{frequency_bucket_mhz(frequency_mhz):.1f}MHz".replace(".", "p")
                + ".npz"
            )
        for path in (run_dir / "tiles").glob(pattern):
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
                rsrp_layers = np.asarray(data["rsrp_dbm"])
                shape = rsrp_layers.shape
                if not (0 <= row < shape[1] and 0 <= col < shape[2]):
                    continue
                cell_data = {
                    "sector_ids": data["sector_ids"],
                    "frequencies_mhz": data["frequencies_mhz"],
                    "rsrp_dbm": rsrp_layers[:, row : row + 1, col : col + 1],
                    "rss_dbm": np.asarray(data["rss_dbm"])[:, row : row + 1, col : col + 1],
                }
                if "bandwidths_mhz" in data:
                    cell_data["bandwidths_mhz"] = data["bandwidths_mhz"]
                if calibration_model is not None:
                    cell_layers = calibrated_layers(
                        cell_data,
                        calibration_model,
                        float(self.settings.raw["receiver"]["noise_figure_db"]),
                        bandwidths,
                    )
                else:
                    cell_layers = cell_data
                    cell_layers["sinr_db"] = np.asarray(data["sinr_db"])[
                        :, row : row + 1, col : col + 1
                    ]
                path_gain_cell = np.asarray(data["path_gain_db"])[:, row, col]
                for index, sector_id in enumerate(data["sector_ids"].astype(str)):
                    item_operator = str(data["operators"][index])
                    item_frequency = float(data["frequencies_mhz"][index])
                    if operator and operator.lower() not in item_operator.lower():
                        continue
                    if frequency_mhz and abs(item_frequency - frequency_mhz) > 5:
                        continue
                    defaults = json.loads(str(data["defaults_applied"][index]))
                    rsrp = float(cell_layers["rsrp_dbm"][index, 0, 0])
                    if not math.isfinite(rsrp):
                        continue
                    candidates.append(
                        SectorPrediction(
                            sector_id=sector_id,
                            operator=item_operator,
                            technology=str(data["technologies"][index]),
                            frequency_mhz=item_frequency,
                            path_gain_db=float(path_gain_cell[index]),
                            received_power_dbm=float(cell_layers["rss_dbm"][index, 0, 0]),
                            rsrp_dbm=rsrp,
                            rssi_dbm=float(cell_layers["rss_dbm"][index, 0, 0]),
                            sinr_db=float(cell_layers["sinr_db"][index, 0, 0]),
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
            model_version=str(run["model_version"])
            + (f"+{calibration_model['model_id']}" if calibration_model else ""),
            run_id=run_id,
            serving_sector_id=ranked[0].sector_id if ranked else None,
            service_available=bool(ranked and ranked[0].rsrp_dbm >= threshold),
            predictions=ranked,
            generated_at=datetime.now(UTC),
            warnings=[f"Applied calibration model {calibration_model['model_id']}."]
            if calibration_model
            else [],
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
        calibration_model: dict[str, object] | None = None,
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
            if calibration_model is not None:
                rss += offset_db(calibration_model, sector.sector_id, sector.tx_frequency_mhz)
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
            model_version="free-space-preview"
            + (f"+{calibration_model['model_id']}" if calibration_model else ""),
            run_id="free-space-preview",
            serving_sector_id=ranked[0].sector_id if ranked else None,
            service_available=bool(ranked and ranked[0].rsrp_dbm >= threshold),
            predictions=ranked,
            generated_at=datetime.now(UTC),
            warnings=[
                "No cached Sionna tile covered this point; values are free-space preview estimates."
            ]
            + (
                [f"Applied calibration model {calibration_model['model_id']}."]
                if calibration_model
                else []
            ),
        )
