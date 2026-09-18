from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Tile:
    tile_id: str
    row: int
    column: int
    center_x_m: float
    center_y_m: float
    size_m: float
    overlap_m: float

    @property
    def simulation_size_m(self) -> float:
        return self.size_m + 2 * self.overlap_m


def make_tiles(width_m: float, tile_size_m: float, overlap_m: float) -> list[Tile]:
    count = math.ceil(width_m / tile_size_m)
    total = count * tile_size_m
    start = -total / 2 + tile_size_m / 2
    return [
        Tile(
            tile_id=f"r{row:03d}-c{column:03d}",
            row=row,
            column=column,
            center_x_m=start + column * tile_size_m,
            center_y_m=start + row * tile_size_m,
            size_m=tile_size_m,
            overlap_m=overlap_m,
        )
        for row in range(count)
        for column in range(count)
    ]
