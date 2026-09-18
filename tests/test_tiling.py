from ottawa_rt.tiling import make_tiles


def test_tiles_cover_square_with_stable_ids():
    tiles = make_tiles(2000, 1000, 50)
    assert len(tiles) == 4
    assert tiles[0].tile_id == "r000-c000"
    assert {tile.center_x_m for tile in tiles} == {-500, 500}
    assert all(tile.simulation_size_m == 1100 for tile in tiles)
