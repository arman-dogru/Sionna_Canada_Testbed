import json
from datetime import UTC, datetime
from pathlib import Path

from ottawa_rt.calibration import calibrate, match_measurement
from ottawa_rt.config import load_settings
from ottawa_rt.models import ReceiverMeasurement, SectorPrediction, SectorRecord
from ottawa_rt.query import calibration_offset_db


def sector(**updates):
    values = {
        "sector_id": "s1",
        "operator": "Carrier",
        "technology": "LTE",
        "latitude": 45.34,
        "longitude": -75.91,
        "tx_frequency_mhz": 1900,
        "bandwidth_mhz": 20,
        "tx_power_dbm": 43,
        "antenna_height_agl_m": 25,
        "antenna_gain_dbi": 15,
        "line_loss_db": 2,
        "azimuth_deg": 0,
        "downtilt_deg": 4,
        "horizontal_beamwidth_deg": 65,
        "vertical_beamwidth_deg": 10,
        "source_snapshot": "test",
    }
    values.update(updates)
    return SectorRecord(**values)


def test_match_uses_identity_and_frequency():
    measurement = ReceiverMeasurement(
        measurement_id="m1",
        timestamp=datetime.now(UTC),
        latitude=45.341,
        longitude=-75.91,
        frequency_mhz=1900,
        operator="Carrier",
        cell_id="cell",
        rsrp_dbm=-90,
    )
    result = match_measurement(
        measurement, [sector(cell_id="cell"), sector(sector_id="s2", cell_id="other")]
    )
    assert result.match_status == "matched"
    assert result.matched_sector_id == "s1"


def test_calibration_recovers_global_offset(tmp_path):
    config = tmp_path / "config" / "default.yaml"
    config.parent.mkdir()
    config.write_text(Path("config/default.yaml").read_text())
    settings = load_settings(config)
    settings.paths.ensure()
    baseline = tmp_path / "baseline.jsonl"
    rows = []
    for index, split in enumerate(["train"] * 10 + ["validation"] * 3 + ["test"] * 3):
        rows.append(
            {
                "measurement_id": f"m{index}",
                "split": split,
                "sector_id": "s1",
                "frequency_mhz": 1900,
                "measured_rsrp_dbm": -90,
                "predicted_rsrp_dbm": -95,
            }
        )
    baseline.write_text("".join(json.dumps(row) + "\n" for row in rows))
    model = calibrate(settings, baseline)
    assert model["global_receiver_offset_db"] == 5
    assert model["metrics"]["test"]["count"] == 3


def test_calibration_offset_combines_stages():
    prediction = SectorPrediction(
        sector_id="sector-1",
        operator="Example",
        technology="LTE",
        frequency_mhz=2115,
        path_gain_db=-100,
        received_power_dbm=-70,
        rsrp_dbm=-95,
        rssi_dbm=-70,
        sinr_db=10,
        rank=1,
        confidence="high",
    )
    model = {
        "global_receiver_offset_db": 2.0,
        "frequency_group_offsets_db": {"1-2.3GHz": -0.5},
        "sector_eirp_offsets_db": {"sector-1": 1.25},
    }

    assert calibration_offset_db(model, prediction) == 2.75
