import math

import numpy as np

from ottawa_rt.stitch import _seam_statistics, _strongest


def test_strongest_server_preserves_no_coverage_cells():
    data = {
        "rsrp_dbm": np.asarray([[[-100.0, -np.inf]], [[-90.0, -np.inf]]]),
        "sector_ids": np.asarray(["a", "b"]),
    }

    values, serving = _strongest(data)

    assert values[0, 0] == -90
    assert serving[0, 0] == "b"
    assert math.isinf(values[0, 1])
    assert serving[0, 1] == ""


def test_seam_statistics_compare_shared_overlap():
    left = np.zeros((4, 4), dtype=np.float32)
    right = np.zeros((4, 4), dtype=np.float32)
    right[:, :2] = 2.0

    report = _seam_statistics({(0, 0): left, (0, 1): right}, overlap_cells=1)

    assert report["sample_count"] == 8
    assert report["mae_db"] == 2.0
