import numpy as np

from ottawa_rt.simulation import maximum_layer, nearest_supported_frequency_hz


def test_maximum_layer_marks_untraced_cells_invalid():
    values = np.asarray([[[-100.0, np.nan]], [[-90.0, -np.inf]]], dtype=np.float32)

    maximum, association, valid = maximum_layer(values)

    assert maximum[0, 0] == -90.0
    assert association[0, 0] == 1
    assert valid[0, 0]
    assert np.isnan(maximum[0, 1])
    assert association[0, 1] == -1
    assert not valid[0, 1]


def test_material_frequency_boundary_is_explicit_and_nearest():
    ranges = [(1.0, 10.0)]
    assert nearest_supported_frequency_hz(ranges, 635e6) == 1e9
    assert nearest_supported_frequency_hz(ranges, 2.6e9) is None
    assert nearest_supported_frequency_hz([(1.0, 40.0), (110.0, 330.0)], 80e9) == 110e9
