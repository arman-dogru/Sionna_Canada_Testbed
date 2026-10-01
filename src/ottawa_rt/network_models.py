"""Explicit assumptions for repeatable downlink traffic experiments."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"


class TrafficProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(pattern=SAFE_ID)
    count: int = Field(ge=1, le=500)
    arrival: Literal["cbr", "poisson", "full_buffer"] = "cbr"
    offered_mbps: float = Field(default=2, gt=0, le=1000)
    packet_bytes: int = Field(default=1200, ge=64, le=9000)
    buffer_packets: int = Field(default=1000, ge=1, le=10000)


class UEPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    ue_id: str = Field(pattern=SAFE_ID)
    profile: str
    x_m: float
    y_m: float


class NetworkScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(pattern=SAFE_ID)
    run_id: str = Field(pattern=SAFE_ID)
    frequency_mhz: float = Field(gt=0)
    operator: str | None = None
    calibrated: bool = False
    channel_source: Literal["paths", "radio_map"] = "paths"
    ray_samples_per_src: int = Field(default=100000, ge=1000, le=10000000)
    ray_max_depth: int = Field(default=4, ge=0, le=8)
    duration_s: float = Field(default=2, gt=0, le=60)
    seed: int = Field(default=41, ge=0, le=2**32 - 1)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    bandwidth_mhz: Literal[5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100] = 20
    numerology: Literal[0, 1] = 1
    data_symbols: int = Field(default=11, ge=1, le=14)
    downlink_fraction: float = Field(default=0.75, gt=0, le=1)
    bler_target: float = Field(default=0.1, gt=0, lt=1)
    noise_figure_db: float = Field(default=7, ge=0, le=30)
    pf_beta: float = Field(default=0.95, gt=0, lt=1)
    core_latency_ms: float = Field(default=5, ge=0, le=1000)
    placement_width_m: float = Field(default=1000, gt=0)
    center_x_m: float = 0
    center_y_m: float = 0
    profiles: list[TrafficProfile] = Field(min_length=1, max_length=10)
    ues: list[UEPosition] | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> NetworkScenario:
        names = [p.name for p in self.profiles]
        if len(set(names)) != len(names):
            raise ValueError("Traffic profile names must be unique")
        count = sum(p.count for p in self.profiles)
        if count > 500:
            raise ValueError("At most 500 UEs per experiment")
        if self.numerology == 0 and self.bandwidth_mhz > 50:
            raise ValueError("15 kHz subcarrier spacing supports at most 50 MHz here")
        slots = self.duration_s / self.slot_s
        if round(slots) < 1 or abs(slots - round(slots)) > 1e-6:
            raise ValueError("duration_s must be a positive whole number of NR slots")
        if self.ues is not None:
            if len(self.ues) != count or len({u.ue_id for u in self.ues}) != count:
                raise ValueError("Explicit UEs must have unique IDs and match profile counts")
            for profile in self.profiles:
                if sum(u.profile == profile.name for u in self.ues) != profile.count:
                    raise ValueError(f"UE count does not match profile {profile.name}")
        return self

    @property
    def slot_s(self) -> float:
        return 0.001 / 2**self.numerology

    @property
    def num_rb(self) -> int:
        # NR FR1 resource-block counts, TS 38.104 Table 5.3.2-1.
        rb15 = {5: 25, 10: 52, 15: 79, 20: 106, 25: 133, 30: 160, 40: 216, 50: 270}
        rb30 = {
            5: 11,
            10: 24,
            15: 38,
            20: 51,
            25: 65,
            30: 78,
            40: 106,
            50: 133,
            60: 162,
            70: 189,
            80: 217,
            90: 245,
            100: 273,
        }
        return (rb15 if self.numerology == 0 else rb30)[self.bandwidth_mhz]
