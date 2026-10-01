import csv
import json
from pathlib import Path

from ottawa_rt.config import load_settings
from ottawa_rt.measurement_snapshot import build_measurement_snapshot


def test_snapshot_filters_area_rat_invalid_rows_and_duplicates(tmp_path):
    config = tmp_path / "config" / "default.yaml"
    config.parent.mkdir()
    config.write_text(Path("config/default.yaml").read_text())
    settings = load_settings(config)
    settings.paths.ensure()
    run_id = "test-run"
    run_dir = settings.paths.runs / run_id
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "width_m": 6000,
                "anchor_wgs84": {"latitude": 45.34, "longitude": -75.91},
            }
        )
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "drive.csv"
    fieldnames = [
        "Record Time (UTC)",
        "Node ID",
        "Operator",
        "Frequency (MHz)",
        "RAT",
        "Latitude",
        "Longitude",
        "PCI/PSC/BSIC",
        "CGI",
        "Cell ID",
        "RSRP/RSCP/RSSCH (dBm)",
        "RSSI (dBm)",
        "RSRQ (dB)",
        "RS SINR (dB)",
    ]
    valid = {
        "Record Time (UTC)": "2026-06-18T13:17:15Z",
        "Node ID": "node-1",
        "Operator": "Carrier",
        "Frequency (MHz)": "1960",
        "RAT": "LTE",
        "Latitude": "45.34",
        "Longitude": "-75.91",
        "PCI/PSC/BSIC": "101.0",
        "CGI": "cgi-1",
        "Cell ID": "cell-1",
        "RSRP/RSCP/RSSCH (dBm)": "-90",
        "RSSI (dBm)": "-65",
        "RSRQ (dB)": "-12",
        "RS SINR (dB)": "10",
    }
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(valid)
        writer.writerow(valid)
        writer.writerow({**valid, "RAT": "3G"})
        writer.writerow({**valid, "Latitude": "49.0", "Longitude": "-123.0"})
        writer.writerow({**valid, "RSRP/RSCP/RSSCH (dBm)": "-1000"})

    result = build_measurement_snapshot(
        settings, inputs, run_id=run_id, snapshot_name="snapshot"
    )

    assert result["counts"]["included"] == 1
    assert result["counts"]["duplicate"] == 1
    assert result["counts"]["non_lte_nr"] == 1
    assert result["counts"]["outside_run_output"] == 1
    assert result["counts"]["invalid_rsrp"] == 1
    output = settings.paths.measurements / "snapshots" / "snapshot" / "receiver-measurements.csv"
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["physical_cell_id"] == "101"
    assert rows[0]["cell_id"] == ""
