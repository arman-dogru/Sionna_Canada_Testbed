import math

from ottawa_rt.geo import (
    angular_difference_deg,
    bbox_from_center,
    free_space_path_loss_db,
    haversine_m,
)


def test_bbox_contains_center_and_has_expected_width():
    box = bbox_from_center(45.3404552, -75.9111420, 2000)
    assert box.contains(45.3404552, -75.9111420)
    east_west = haversine_m(45.3404552, box.west, 45.3404552, box.east)
    assert 1990 < east_west < 2010


def test_free_space_loss_and_angle_wrap():
    assert math.isclose(free_space_path_loss_db(1000, 1000), 92.44, abs_tol=0.02)
    assert angular_difference_deg(355, 5) == 10
