from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SectorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector_id: str
    licence_number: str | None = None
    operator: str
    technology: str
    cell_id: str | None = None
    physical_cell_id: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    tx_frequency_mhz: float = Field(gt=0)
    bandwidth_mhz: float | None = Field(default=None, gt=0)
    tx_power_dbm: float
    antenna_height_agl_m: float = Field(ge=-20, le=500)
    antenna_gain_dbi: float
    line_loss_db: float
    azimuth_deg: float = Field(ge=0, le=360)
    downtilt_deg: float = Field(ge=-90, le=90)
    horizontal_beamwidth_deg: float = Field(gt=0, le=360)
    vertical_beamwidth_deg: float = Field(gt=0, le=180)
    is_omnidirectional: bool = False
    source_record_id: str | None = None
    source_snapshot: str
    defaults_applied: list[str] = Field(default_factory=list)

    @property
    def eirp_dbm(self) -> float:
        return self.tx_power_dbm + self.antenna_gain_dbi - self.line_loss_db


class ReceiverMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_id: str
    timestamp: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    height_agl_m: float = Field(default=1.5, ge=-5, le=200)
    frequency_mhz: float = Field(gt=0)
    operator: str | None = None
    cell_id: str | None = None
    physical_cell_id: str | None = None
    rsrp_dbm: float | None = Field(default=None, ge=-200, le=20)
    rssi_dbm: float | None = Field(default=None, ge=-200, le=50)
    rsrq_db: float | None = Field(default=None, ge=-60, le=20)
    sinr_db: float | None = Field(default=None, ge=-60, le=100)
    matched_sector_id: str | None = None
    match_status: Literal["unmatched", "matched", "ambiguous"] = "unmatched"
    split: Literal["train", "validation", "test"] | None = None

    @field_validator("operator", "cell_id", "physical_cell_id", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        return None if value == "" else value


class SectorPrediction(BaseModel):
    sector_id: str
    operator: str
    technology: str
    frequency_mhz: float
    path_gain_db: float
    received_power_dbm: float
    rsrp_dbm: float
    rssi_dbm: float
    sinr_db: float
    rank: int
    confidence: Literal["high", "medium", "low"]
    defaults_applied: list[str] = Field(default_factory=list)
    paths: list[dict[str, float | str]] | None = None


class ServicePrediction(BaseModel):
    latitude: float
    longitude: float
    height_agl_m: float
    model_version: str
    run_id: str
    serving_sector_id: str | None
    service_available: bool
    predictions: list[SectorPrediction]
    generated_at: datetime
    warnings: list[str] = Field(default_factory=list)


class ProvenanceEntry(BaseModel):
    name: str
    source_url: str
    retrieved_at: datetime
    local_path: str
    sha256: str
    size_bytes: int
    licence: str
    metadata: dict[str, object] = Field(default_factory=dict)
