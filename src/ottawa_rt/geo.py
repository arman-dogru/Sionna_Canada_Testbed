from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.south <= latitude <= self.north and self.west <= longitude <= self.east


def bbox_from_center(latitude: float, longitude: float, width_m: float) -> BoundingBox:
    half = width_m / 2.0
    lat_delta = math.degrees(half / EARTH_RADIUS_M)
    lon_delta = math.degrees(half / (EARTH_RADIUS_M * math.cos(math.radians(latitude))))
    return BoundingBox(
        south=latitude - lat_delta,
        west=longitude - lon_delta,
        north=latitude + lat_delta,
        east=longitude + lon_delta,
    )


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_difference_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def free_space_path_loss_db(distance_m: float, frequency_mhz: float) -> float:
    distance_km = max(distance_m, 1.0) / 1000.0
    return 32.44 + 20.0 * math.log10(distance_km) + 20.0 * math.log10(frequency_mhz)


def thermal_noise_dbm(bandwidth_mhz: float, noise_figure_db: float = 7.0) -> float:
    bandwidth_hz = max(bandwidth_mhz, 0.001) * 1e6
    return -174.0 + 10.0 * math.log10(bandwidth_hz) + noise_figure_db


def band_label(frequency_mhz: float) -> str:
    # Stable frequency groups for simulation/calibration; this does not claim a 3GPP band number.
    if frequency_mhz < 1000:
        return "sub-1GHz"
    if frequency_mhz < 2300:
        return "1-2.3GHz"
    if frequency_mhz < 3300:
        return "2.3-3.3GHz"
    if frequency_mhz < 4200:
        return "3.3-4.2GHz"
    return "above-4.2GHz"
