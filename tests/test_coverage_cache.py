import numpy as np
from PIL import Image

from ottawa_rt.api import _write_coverage_png


def test_coverage_png_is_rgba_transparent_and_north_up(tmp_path):
    values = np.asarray([[-145.0, -95.0], [-45.0, -np.inf]], dtype=np.float32)
    output = tmp_path / "coverage.png"

    _write_coverage_png(output, values, "rsrp")

    with Image.open(output) as image:
        pixels = np.asarray(image)
    assert pixels.shape == (2, 2, 4)
    assert pixels[0, 1, 3] == 0
    assert pixels[0, 0, 3] == 220
    assert pixels[1, 0, 3] == 64
    assert pixels[0, 0, :3].tolist() == [253, 231, 37]
