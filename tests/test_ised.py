import csv
import io
import zipfile

from ottawa_rt.data.ised import iter_sectors, normalize_ised
from ottawa_rt.geo import BoundingBox


def test_streaming_ised_normalization_and_defaults(tmp_path):
    headers = [
        "licence_number",
        "licensee_name",
        "technology",
        "cell_id",
        "physical_id",
        "latitude",
        "longitude",
        "record_id",
        "tx_frequency",
        "bandwidth",
        "tx_power",
        "tx_ant_height",
        "tx_ant_gain",
        "tx_line_loss",
        "tx_ant_omni_indicator",
        "tx_ant_horiz_beamwidth",
        "tx_ant_vert_beamwidth",
        "tx_ant_azimuth",
        "tx_ant_elevation_angle",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerow(
        {
            "licence_number": "L1",
            "licensee_name": "Example Mobile",
            "technology": "LTE",
            "cell_id": "C1",
            "physical_id": "42",
            "latitude": "45.34",
            "longitude": "-75.91",
            "record_id": "R1",
            "tx_frequency": "1967.5",
            "bandwidth": "20",
            "tx_power": "43",
            "tx_ant_height": "",
            "tx_ant_gain": "15",
            "tx_line_loss": "2",
            "tx_ant_omni_indicator": "D",
            "tx_ant_horiz_beamwidth": "65",
            "tx_ant_vert_beamwidth": "10",
            "tx_ant_azimuth": "120",
            "tx_ant_elevation_angle": "4",
        }
    )
    writer.writerow(
        {
            "licence_number": "L2",
            "licensee_name": "Not Mobile",
            "technology": "FIXED",
            "latitude": "45.34",
            "longitude": "-75.91",
            "tx_frequency": "6000",
        }
    )
    archive = tmp_path / "ised.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("extract.csv", buffer.getvalue())
    output = tmp_path / "sectors.jsonl"
    defaults = {
        "tx_power_dbm": 43,
        "height_agl_m": 25,
        "horizontal_beamwidth_deg": 65,
        "vertical_beamwidth_deg": 10,
        "gain_dbi": 15,
        "line_loss_db": 2,
        "downtilt_deg": 4,
    }
    summary = normalize_ised(archive, output, BoundingBox(45, -76, 46, -75), defaults)
    assert summary["sector_count"] == 1
    assert summary["sectors_with_defaults"] == 1
    assert "contact_name" not in output.read_text()


def test_ised_schema_asterisks_are_removed(tmp_path):
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "licensee_name*",
            "technology",
            "latitude",
            "longitude",
            "tx_frequency",
            "record_id",
            "tx_ant_omni_indicator",
        ],
    )
    writer.writeheader()
    writer.writerow(
        {
            "licensee_name*": "Example Mobile Inc.",
            "technology": "LTE",
            "latitude": "45.34",
            "longitude": "-75.91",
            "tx_frequency": "2115",
            "record_id": "F1",
            "tx_ant_omni_indicator": "O",
        }
    )
    archive = tmp_path / "ised.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("extract.csv", buffer.getvalue())

    defaults = {
        "tx_power_dbm": 43,
        "height_agl_m": 25,
        "horizontal_beamwidth_deg": 65,
        "vertical_beamwidth_deg": 10,
        "gain_dbi": 15,
        "line_loss_db": 2,
        "downtilt_deg": 4,
    }
    sectors = list(iter_sectors(archive, BoundingBox(45, -76, 46, -75), defaults))

    assert sectors[0].operator == "Example Mobile Inc."
