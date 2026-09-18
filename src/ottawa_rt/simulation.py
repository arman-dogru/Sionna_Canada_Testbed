from __future__ import annotations

import json
import math
import os
import time
import traceback
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.geo import bbox_from_center, free_space_path_loss_db, haversine_m, thermal_noise_dbm
from ottawa_rt.jobs import FileJobQueue, SimulationJob
from ottawa_rt.models import SectorRecord
from ottawa_rt.tiling import Tile, make_tiles


def frequency_bucket_mhz(frequency_mhz: float) -> float:
    return round(frequency_mhz / 5.0) * 5.0


def nearest_supported_frequency_hz(
    ranges_ghz: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    requested_hz: float,
) -> float | None:
    """Return ``None`` when supported, otherwise the nearest documented boundary."""
    requested_ghz = requested_hz / 1e9
    if any(low <= requested_ghz <= high for low, high in ranges_ghz):
        return None
    boundaries = [boundary for limits in ranges_ghz for boundary in limits]
    return min(boundaries, key=lambda boundary: abs(boundary - requested_ghz)) * 1e9


def maximum_layer(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the finite maximum, source index, and validity mask for every receiver cell."""
    finite = np.isfinite(values)
    valid = np.any(finite, axis=0)
    safe = np.where(finite, values, -np.inf)
    association = np.argmax(safe, axis=0).astype(np.int32)
    maximum = np.take_along_axis(safe, association[None, ...], axis=0)[0].astype(np.float32)
    maximum[~valid] = np.nan
    association[~valid] = -1
    return maximum, association, valid


def resource_blocks(bandwidth_mhz: float | None) -> int:
    if bandwidth_mhz is None:
        return 100
    table = [(1.4, 6), (3, 15), (5, 25), (10, 50), (15, 75), (20, 100), (40, 216), (100, 273)]
    return min(table, key=lambda item: abs(item[0] - bandwidth_mhz))[1]


def rsrp_from_rss_dbm(rss_dbm: np.ndarray, bandwidth_mhz: float | None) -> np.ndarray:
    elements = max(resource_blocks(bandwidth_mhz) * 12, 1)
    return rss_dbm - 10.0 * np.log10(elements)


def sector_pattern_gain_db(
    sector: SectorRecord,
    receiver_x: np.ndarray,
    receiver_y: np.ndarray,
    receiver_z: np.ndarray,
    tx_position: tuple[float, float, float],
    front_to_back_db: float = 30.0,
) -> np.ndarray:
    """Approximate the reported sector pattern using 3GPP-style quadratic cuts."""
    dx = receiver_x - tx_position[0]
    dy = receiver_y - tx_position[1]
    horizontal_distance = np.maximum(np.hypot(dx, dy), 1.0)
    bearing = np.mod(np.degrees(np.arctan2(dx, dy)), 360.0)
    azimuth_delta = np.abs(np.mod(bearing - sector.azimuth_deg + 180.0, 360.0) - 180.0)
    horizontal_loss = 12.0 * np.square(azimuth_delta / sector.horizontal_beamwidth_deg)
    depression = np.degrees(np.arctan2(tx_position[2] - receiver_z, horizontal_distance))
    elevation_delta = depression - sector.downtilt_deg
    vertical_loss = 12.0 * np.square(elevation_delta / sector.vertical_beamwidth_deg)
    attenuation = np.minimum(horizontal_loss + vertical_loss, front_to_back_db)
    return sector.antenna_gain_dbi - attenuation


def _load_scene_metadata(scene_xml: Path) -> dict[str, object]:
    return json.loads((scene_xml.parent / "scene_metadata.json").read_text(encoding="utf-8"))


def _build_measurement_surface(
    settings: Settings,
    metadata: dict[str, object],
    tile: Tile,
    destination: Path,
) -> tuple[int, int]:
    """Create a two-triangle-per-cell receiver mesh at configured height AGL."""
    try:
        import rasterio
        import trimesh
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Terrain-draped radio maps require the 'geo' dependency group") from exc
    cell = settings.cell_size_m
    cells = round(tile.simulation_size_m / cell)
    half = tile.simulation_size_m / 2.0
    x_edges = np.linspace(tile.center_x_m - half, tile.center_x_m + half, cells + 1)
    y_edges = np.linspace(tile.center_y_m - half, tile.center_y_m + half, cells + 1)
    x_grid, y_grid = np.meshgrid(x_edges, y_edges)
    origin = metadata["local_origin"]
    utm_x = x_grid + float(origin["easting"])
    utm_y = y_grid + float(origin["northing"])
    dtm_path = settings.paths.raw / "terrain" / "dtm.tif"
    with rasterio.open(dtm_path) as source:
        to_dtm = Transformer.from_crs(metadata["target_crs"], source.crs, always_xy=True)
        sx, sy = to_dtm.transform(utm_x.ravel(), utm_y.ravel())
        elevation = np.asarray(
            [value[0] for value in source.sample(zip(sx, sy, strict=True))]
        ).reshape(x_grid.shape)
        invalid = ~np.isfinite(elevation)
        if source.nodata is not None:
            invalid |= elevation == source.nodata
    if invalid.any():
        elevation[invalid] = float(origin["elevation_m"])
    z_grid = elevation - float(origin["elevation_m"]) + settings.default_receiver_height_m
    vertices = np.column_stack((x_grid.ravel(), y_grid.ravel(), z_grid.ravel()))
    faces = []
    stride = cells + 1
    for row in range(cells):
        for column in range(cells):
            lower_left = row * stride + column
            lower_right = lower_left + 1
            upper_left = lower_left + stride
            upper_right = upper_left + 1
            faces.extend(
                [(lower_left, lower_right, upper_right), (lower_left, upper_right, upper_left)]
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)
    mesh.export(destination)
    x_centers = (x_edges[:-1] + x_edges[1:]) / 2.0
    y_centers = (y_edges[:-1] + y_edges[1:]) / 2.0
    receiver_z = (z_grid[:-1, :-1] + z_grid[:-1, 1:] + z_grid[1:, :-1] + z_grid[1:, 1:]) / 4.0
    np.savez_compressed(
        destination.with_suffix(".npz"),
        x_m=x_centers,
        y_m=y_centers,
        receiver_z_m=receiver_z,
        rows=np.asarray(cells),
        columns=np.asarray(cells),
    )
    return cells, cells


def prepare_run(
    settings: Settings,
    *,
    width_m: float,
    run_id: str | None = None,
    frequency_groups_mhz: list[float] | None = None,
) -> dict[str, object]:
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = settings.paths.runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    scene_xml = settings.paths.scenes / f"legget-{int(width_m)}m" / "scene.xml"
    if not scene_xml.exists():
        raise FileNotFoundError(f"Scene not found: {scene_xml}. Run build-scene first.")
    sector_path = settings.paths.processed / "sectors.jsonl"
    sectors = load_sectors(sector_path)
    if not sectors:
        raise FileNotFoundError(
            f"No normalized sectors found: {sector_path}. Run normalize-ised first."
        )

    selected: list[SectorRecord] = []
    output_bbox = bbox_from_center(*settings.anchor, width_m)
    threshold = float(settings.raw["simulation"]["min_free_space_rx_dbm"])
    corners = [
        (output_bbox.south, output_bbox.west),
        (output_bbox.south, output_bbox.east),
        (output_bbox.north, output_bbox.west),
        (output_bbox.north, output_bbox.east),
        settings.anchor,
    ]
    for sector in sectors:
        nearest = min(
            haversine_m(sector.latitude, sector.longitude, lat, lon) for lat, lon in corners
        )
        upper_bound = sector.eirp_dbm - free_space_path_loss_db(nearest, sector.tx_frequency_mhz)
        if upper_bound >= threshold:
            selected.append(sector)

    groups: dict[float, list[SectorRecord]] = defaultdict(list)
    for sector in selected:
        groups[frequency_bucket_mhz(sector.tx_frequency_mhz)].append(sector)
    requested_groups: list[float] = []
    if frequency_groups_mhz:
        requested_groups = sorted({frequency_bucket_mhz(value) for value in frequency_groups_mhz})
        missing = [value for value in requested_groups if value not in groups]
        if missing:
            raise ValueError(
                f"Requested frequency groups are unavailable: {missing}. "
                f"Available groups: {sorted(groups)}"
            )
        groups = {value: groups[value] for value in requested_groups}
    run_sectors = [sector for group in groups.values() for sector in group]
    area = settings.raw["area"]
    tiles = make_tiles(width_m, float(area["tile_size_m"]), float(area["overlap_m"]))
    queue = FileJobQueue(run_dir / "queue")
    output_dir = run_dir / "tiles"
    output_dir.mkdir(exist_ok=True)
    metadata = _load_scene_metadata(scene_xml)
    surface_dir = run_dir / "surfaces"
    surface_dir.mkdir(exist_ok=True)
    for tile in tiles:
        surface_path = surface_dir / f"{tile.tile_id}.ply"
        if not surface_path.exists() or not surface_path.with_suffix(".npz").exists():
            _build_measurement_surface(settings, metadata, tile, surface_path)
    for frequency, group in sorted(groups.items()):
        for tile in tiles:
            job_id = f"{tile.tile_id}-{frequency:.1f}MHz".replace(".", "p")
            queue.enqueue(
                SimulationJob(
                    job_id=job_id,
                    run_id=run_id,
                    scene_xml=settings.portable_path(scene_xml),
                    tile=tile.__dict__,
                    frequency_mhz=frequency,
                    sector_ids=[sector.sector_id for sector in group],
                    output_path=settings.portable_path(output_dir / f"{job_id}.npz"),
                    measurement_surface=settings.portable_path(surface_dir / f"{tile.tile_id}.ply"),
                )
            )
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(),
        "model_version": settings.raw["project"]["model_version"],
        "scene_xml": settings.portable_path(scene_xml),
        "width_m": width_m,
        "anchor_wgs84": {"latitude": settings.anchor[0], "longitude": settings.anchor[1]},
        "cell_size_m": settings.cell_size_m,
        "receiver_surface": "terrain-draped",
        "tile_count": len(tiles),
        "frequency_groups_mhz": sorted(groups),
        "requested_frequency_groups_mhz": requested_groups,
        "sector_count": len(run_sectors),
        "sector_ids": [sector.sector_id for sector in run_sectors],
        "simulation_parameters": {
            "samples_per_tx": int(settings.raw["simulation"]["samples_per_tx"]),
            "max_depth": int(settings.raw["simulation"]["max_depth"]),
            "los": bool(settings.raw["simulation"]["los"]),
            "specular_reflection": bool(settings.raw["simulation"]["specular_reflection"]),
            "diffuse_reflection": bool(settings.raw["simulation"]["diffuse_reflection"]),
            "refraction": bool(settings.raw["simulation"]["refraction"]),
            "diffraction": bool(settings.raw["simulation"]["diffraction"]),
            "material_out_of_range_policy": "nearest-valid-itu-p2040-boundary",
        },
        "visualization_schema_version": 1,
        "queue": queue.counts(),
    }
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _terrain_height_local(
    settings: Settings, metadata: dict[str, object], sector: SectorRecord
) -> float:
    try:
        import rasterio
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Simulation requires the 'geo' dependency group") from exc
    dtm_path = settings.paths.raw / "terrain" / "dtm.tif"
    with rasterio.open(dtm_path) as source:
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        x, y = transformer.transform(sector.longitude, sector.latitude)
        elevation = float(next(source.sample([(x, y)]))[0])
    origin_z = float(metadata["local_origin"]["elevation_m"])
    return elevation - origin_z


def _local_xy(
    metadata: dict[str, object], latitude: float, longitude: float
) -> tuple[float, float]:
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Simulation requires the 'geo' dependency group") from exc
    transformer = Transformer.from_crs("EPSG:4326", metadata["target_crs"], always_xy=True)
    easting, northing = transformer.transform(longitude, latitude)
    origin = metadata["local_origin"]
    return easting - float(origin["easting"]), northing - float(origin["northing"])


def execute_job(settings: Settings, job: SimulationJob) -> dict[str, object]:
    """Execute a single radio-map job. Import Sionna only after GPU isolation is configured."""
    started = time.perf_counter()
    try:
        import drjit as dr
        import mitsuba as mi
        from sionna.rt import PlanarArray, RadioMapSolver, Transmitter, load_mesh, load_scene
    except ImportError as exc:  # pragma: no cover - GPU dependency guard
        raise RuntimeError(
            "Sionna RT runtime is not installed; install the 'rt' dependency group"
        ) from exc

    scene_xml = settings.resolve_path(job.scene_xml)
    metadata = _load_scene_metadata(scene_xml)
    all_sectors = {
        sector.sector_id: sector
        for sector in load_sectors(settings.paths.processed / "sectors.jsonl")
    }
    sectors = [all_sectors[item] for item in job.sector_ids if item in all_sectors]
    if not sectors:
        raise RuntimeError(f"Job {job.job_id} has no resolvable transmitter sectors")

    scene = load_scene(str(scene_xml), merge_shapes=True)
    requested_frequency_hz = job.frequency_mhz * 1e6
    material_frequency_fallbacks: list[dict[str, object]] = []
    # Several ITU-R P.2040 fits used by Sionna begin at 1 GHz. Keep the RF
    # carrier at its real value, but freeze only an unsupported material's
    # constitutive parameters at its nearest documented fit boundary.
    from sionna.rt.radio_materials.itu import ITU_MATERIALS_PROPERTIES, itu_material

    for name, material in scene.radio_materials.items():
        itu_type = getattr(material, "itu_type", None)
        if itu_type not in ITU_MATERIALS_PROPERTIES:
            continue
        reference_hz = nearest_supported_frequency_hz(
            list(ITU_MATERIALS_PROPERTIES[itu_type]), requested_frequency_hz
        )
        if reference_hz is None:
            continue
        material.frequency_update_callback = (
            lambda _frequency, material_type=itu_type, fixed_hz=reference_hz: itu_material(
                material_type, fixed_hz
            )
        )
        material_frequency_fallbacks.append(
            {
                "material_name": name,
                "itu_type": itu_type,
                "requested_frequency_mhz": job.frequency_mhz,
                "reference_frequency_mhz": reference_hz / 1e6,
                "policy": "nearest-valid-itu-p2040-boundary",
            }
        )
    scene.frequency = requested_frequency_hz
    representative_bandwidth = float(np.median([item.bandwidth_mhz or 20.0 for item in sectors]))
    scene.bandwidth = representative_bandwidth * 1e6
    scene.temperature = float(settings.raw["receiver"]["temperature_k"])
    scene.tx_array = PlanarArray(
        num_rows=1,
        num_cols=1,
        vertical_spacing=0.5,
        horizontal_spacing=0.5,
        pattern="iso",
        polarization="V",
    )
    tx_names: list[str] = []
    tx_positions: list[tuple[float, float, float]] = []
    for index, sector in enumerate(sectors):
        x, y = _local_xy(metadata, sector.latitude, sector.longitude)
        z = _terrain_height_local(settings, metadata, sector) + sector.antenna_height_agl_m
        name = f"tx-{index:05d}"
        tx = Transmitter(name=name, position=[x, y, z], power_dbm=0.0)
        scene.add(tx)
        azimuth = math.radians(sector.azimuth_deg)
        horizontal = 1000.0
        tx.look_at(
            [
                x + math.sin(azimuth) * horizontal,
                y + math.cos(azimuth) * horizontal,
                z - math.tan(math.radians(sector.downtilt_deg)) * horizontal,
            ]
        )
        tx_names.append(name)
        tx_positions.append((x, y, z))

    tile = Tile(**job.tile)
    sim = settings.raw["simulation"]
    solver = RadioMapSolver()
    if not job.measurement_surface:
        raise RuntimeError(f"Job {job.job_id} does not define a terrain-draped measurement surface")
    surface_path = settings.resolve_path(job.measurement_surface)
    surface_data = np.load(surface_path.with_suffix(".npz"), allow_pickle=False)
    rows, columns = int(surface_data["rows"]), int(surface_data["columns"])
    measurement_surface = load_mesh(str(surface_path))
    radio_map = solver(
        scene=scene,
        measurement_surface=measurement_surface,
        samples_per_tx=int(sim["samples_per_tx"]),
        max_depth=int(sim["max_depth"]),
        los=bool(sim["los"]),
        specular_reflection=bool(sim["specular_reflection"]),
        diffuse_reflection=bool(sim["diffuse_reflection"]),
        refraction=bool(sim["refraction"]),
        diffraction=bool(sim["diffraction"]),
        edge_diffraction=bool(sim["edge_diffraction"]),
        seed=int(settings.raw["compute"]["deterministic_seed"]),
    )
    path_gain_faces = np.asarray(radio_map.path_gain, dtype=np.float32)
    expected_faces = rows * columns * 2
    if path_gain_faces.shape[-1] != expected_faces:
        raise RuntimeError(
            f"Measurement surface returned {path_gain_faces.shape[-1]} cells; expected {expected_faces}"
        )
    path_gain = path_gain_faces.reshape(len(sectors), rows, columns, 2).mean(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        path_gain_db = 10.0 * np.log10(path_gain)
    receiver_x, receiver_y = np.meshgrid(surface_data["x_m"], surface_data["y_m"])
    receiver_z = np.asarray(surface_data["receiver_z_m"])
    front_to_back = float(settings.raw["antenna_defaults"].get("front_to_back_db", 30.0))
    rss_layers = []
    for index, sector in enumerate(sectors):
        gain = sector_pattern_gain_db(
            sector, receiver_x, receiver_y, receiver_z, tx_positions[index], front_to_back
        )
        rss_layers.append(path_gain_db[index] + sector.tx_power_dbm - sector.line_loss_db + gain)
    rss_dbm = np.asarray(rss_layers, dtype=np.float32)
    bandwidths = np.asarray(
        [sector.bandwidth_mhz or representative_bandwidth for sector in sectors]
    )
    rsrp_dbm = np.stack([rsrp_from_rss_dbm(rss_dbm[i], bandwidths[i]) for i in range(len(sectors))])
    signal_mw = np.power(10.0, rss_dbm / 10.0)
    total_mw = np.sum(signal_mw, axis=0)
    noise_figure = float(settings.raw["receiver"]["noise_figure_db"])
    sinr_layers = []
    for index, bandwidth in enumerate(bandwidths):
        noise_mw = 10.0 ** (thermal_noise_dbm(float(bandwidth), noise_figure) / 10.0)
        denominator = np.maximum(total_mw - signal_mw[index] + noise_mw, np.finfo(np.float32).tiny)
        with np.errstate(divide="ignore", invalid="ignore"):
            sinr_layers.append(10.0 * np.log10(signal_mw[index] / denominator))
    sinr_db = np.asarray(sinr_layers, dtype=np.float32)
    max_path_gain_db, path_gain_association, path_gain_valid = maximum_layer(path_gain_db)
    max_rss_dbm, rss_association, rss_valid = maximum_layer(rss_dbm)
    max_rsrp_dbm, rsrp_association, rsrp_valid = maximum_layer(rsrp_dbm)
    max_sinr_db, sinr_association, sinr_valid = maximum_layer(sinr_db)
    ray_hit_count = np.sum(np.isfinite(path_gain_db), axis=0).astype(np.uint16)

    from pyproj import Transformer

    origin = metadata["local_origin"]
    to_wgs84 = Transformer.from_crs(metadata["target_crs"], "EPSG:4326", always_xy=True)

    def bounds_wgs84(half_width: float) -> np.ndarray:
        corners = [
            to_wgs84.transform(
                float(origin["easting"]) + tile.center_x_m + dx,
                float(origin["northing"]) + tile.center_y_m + dy,
            )
            for dx, dy in (
                (-half_width, -half_width),
                (half_width, -half_width),
                (half_width, half_width),
                (-half_width, half_width),
            )
        ]
        return np.asarray(
            [
                min(point[0] for point in corners),
                min(point[1] for point in corners),
                max(point[0] for point in corners),
                max(point[1] for point in corners),
            ],
            dtype=np.float64,
        )

    output = settings.resolve_path(job.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        path_gain_db=path_gain_db,
        rss_dbm=rss_dbm,
        rsrp_dbm=rsrp_dbm,
        sinr_db=sinr_db,
        association=rsrp_association,
        max_path_gain_db=max_path_gain_db,
        max_rss_dbm=max_rss_dbm,
        max_rsrp_dbm=max_rsrp_dbm,
        max_sinr_db=max_sinr_db,
        path_gain_association=path_gain_association,
        rss_association=rss_association,
        rsrp_association=rsrp_association,
        sinr_association=sinr_association,
        path_gain_valid=path_gain_valid,
        rss_valid=rss_valid,
        rsrp_valid=rsrp_valid,
        sinr_valid=sinr_valid,
        ray_hit_count=ray_hit_count,
        sector_ids=np.asarray([sector.sector_id for sector in sectors]),
        operators=np.asarray([sector.operator for sector in sectors]),
        technologies=np.asarray([sector.technology for sector in sectors]),
        frequencies_mhz=np.asarray([sector.tx_frequency_mhz for sector in sectors]),
        defaults_applied=np.asarray([json.dumps(sector.defaults_applied) for sector in sectors]),
        tile_center=np.asarray([tile.center_x_m, tile.center_y_m]),
        tile_size_m=np.asarray(tile.size_m),
        overlap_m=np.asarray(tile.overlap_m),
        cell_size_m=np.asarray(settings.cell_size_m),
        receiver_height_agl_m=np.asarray(settings.default_receiver_height_m),
        receiver_z_m=receiver_z,
        x_m=np.asarray(surface_data["x_m"]),
        y_m=np.asarray(surface_data["y_m"]),
        core_bounds_wgs84=bounds_wgs84(tile.size_m / 2.0),
        simulation_bounds_wgs84=bounds_wgs84(tile.simulation_size_m / 2.0),
        anchor_wgs84=np.asarray([settings.anchor[1], settings.anchor[0]]),
        local_origin_utm_m=np.asarray([origin["easting"], origin["northing"]]),
        local_origin_elevation_m=np.asarray(origin["elevation_m"]),
        vertical_reference=np.asarray("NRCan DTM orthometric elevation plus receiver AGL"),
        tx_positions_local_m=np.asarray(tx_positions, dtype=np.float32),
        tx_latitudes=np.asarray([sector.latitude for sector in sectors]),
        tx_longitudes=np.asarray([sector.longitude for sector in sectors]),
        tx_heights_agl_m=np.asarray([sector.antenna_height_agl_m for sector in sectors]),
        tx_azimuths_deg=np.asarray([sector.azimuth_deg for sector in sectors]),
        tx_downtilts_deg=np.asarray([sector.downtilt_deg for sector in sectors]),
        antenna_pattern=np.asarray("ised-quadratic-cuts"),
        trace_frequency_mhz=np.asarray(job.frequency_mhz),
        material_frequency_fallbacks=np.asarray(json.dumps(material_frequency_fallbacks)),
        visualization_schema_version=np.asarray(1),
    )
    dr.sync_thread()
    return {
        "elapsed_seconds": time.perf_counter() - started,
        "output_bytes": output.stat().st_size,
        "transmitter_count": len(sectors),
        "shape": list(rsrp_dbm.shape),
        "mitsuba_variant": mi.variant(),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
        "samples_per_tx": int(sim["samples_per_tx"]),
        "max_depth": int(sim["max_depth"]),
        "material_frequency_fallbacks": material_frequency_fallbacks,
    }


def worker(settings: Settings, run_id: str, *, once: bool = False) -> dict[str, int]:
    queue = FileJobQueue(settings.paths.runs / run_id / "queue")
    while True:
        claimed = queue.claim()
        if claimed is None:
            break
        job, path = claimed
        try:
            metrics = execute_job(settings, job)
            queue.finish(path, metrics=metrics)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - worker boundary
            queue.finish(path, error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        if once:
            break
    counts = queue.counts()
    run_path = settings.paths.runs / run_id / "run.json"
    if run_path.exists():
        manifest = json.loads(run_path.read_text(encoding="utf-8"))
        manifest["queue"] = counts
        manifest["status"] = (
            "complete"
            if counts["pending"] == 0 and counts["processing"] == 0 and counts["failed"] == 0
            else "incomplete"
        )
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        run_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return counts
