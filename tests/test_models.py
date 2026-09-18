import math

import numpy as np

from ottawa_rt.models import SectorRecord
from ottawa_rt.simulation import sector_pattern_gain_db


def test_eirp_accounting():
    record = SectorRecord(
        sector_id="s",
        operator="Carrier",
        technology="5G",
        latitude=45,
        longitude=-75,
        tx_frequency_mhz=3500,
        tx_power_dbm=43,
        antenna_height_agl_m=30,
        antenna_gain_dbi=17,
        line_loss_db=2,
        azimuth_deg=0,
        downtilt_deg=4,
        horizontal_beamwidth_deg=65,
        vertical_beamwidth_deg=10,
        source_snapshot="test",
    )
    assert record.eirp_dbm == 58


def test_sector_pattern_honours_azimuth_tilt_and_front_to_back_cap():
    record = SectorRecord(
        sector_id="s",
        operator="Carrier",
        technology="LTE",
        latitude=45,
        longitude=-75,
        tx_frequency_mhz=2100,
        tx_power_dbm=43,
        antenna_height_agl_m=30,
        antenna_gain_dbi=17,
        line_loss_db=2,
        azimuth_deg=0,
        downtilt_deg=4,
        horizontal_beamwidth_deg=65,
        vertical_beamwidth_deg=10,
        source_snapshot="test",
    )
    boresight_z = 30 - math.tan(math.radians(4)) * 100
    gains = sector_pattern_gain_db(
        record,
        np.asarray([[0.0, 0.0]]),
        np.asarray([[100.0, -100.0]]),
        np.asarray([[boresight_z, boresight_z]]),
        (0.0, 0.0, 30.0),
        front_to_back_db=30,
    )

    assert math.isclose(float(gains[0, 0]), 17.0, abs_tol=0.01)
    assert math.isclose(float(gains[0, 1]), -13.0, abs_tol=0.01)
