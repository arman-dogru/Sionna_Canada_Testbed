import math

import numpy as np

from ottawa_rt.stitch import _compact_summary, _finite_max, _seam_statistics, _strongest


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


def test_finite_max_uses_any_reached_band():
    first = np.asarray([[np.nan, -100.0], [-np.inf, -90.0]], dtype=np.float32)
    second = np.asarray([[-80.0, np.nan], [-70.0, -95.0]], dtype=np.float32)

    combined = _finite_max([first, second])

    np.testing.assert_array_equal(combined, np.asarray([[-80.0, -100.0], [-70.0, -90.0]]))


def test_compact_summary_reserves_zero_for_no_coverage():
    data = {
        "path_gain_db": np.asarray([[[-110.0, -np.inf]], [[-100.0, -np.inf]]]),
        "rss_dbm": np.asarray([[[-90.0, -np.inf]], [[-80.0, -np.inf]]]),
        "rsrp_dbm": np.asarray([[[-100.0, -np.inf]], [[-90.0, -np.inf]]]),
        "sinr_db": np.asarray([[[10.0, -np.inf]], [[20.0, -np.inf]]]),
        "sector_ids": np.asarray(["sector-a", "sector-b"]),
    }
    sector_ids = [""]
    metrics, serving = _compact_summary(data, {"": 0}, sector_ids)

    assert metrics["max_rsrp_dbm"][0, 0] == -90.0
    assert serving.dtype == np.uint32
    assert serving.tolist() == [[2, 0]]
    assert sector_ids == ["", "sector-a", "sector-b"]
