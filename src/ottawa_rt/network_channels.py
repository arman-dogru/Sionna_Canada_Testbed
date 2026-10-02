"""Trace SISO channel impulse responses at exact terrain-relative UE positions."""

from __future__ import annotations

import json
import math
import time
from xml.etree import ElementTree

import numpy as np

from ottawa_rt.data.ised import load_sectors
from ottawa_rt.data.provenance import sha256_file
from ottawa_rt.geo import thermal_noise_dbm
from ottawa_rt.simulation import (
    _local_xy,
    _terrain_height_local,
    frequency_bucket_mhz,
    nearest_supported_frequency_hz,
    sector_pattern_gain_db,
)


def _ue_terrain_elevations(source, ues: list[dict]) -> tuple[list[float], list[dict]]:
    """Retain UE locations, with explicit reuse of saved RF heights at DTM holes."""
    import rasterio
    from pyproj import Transformer

    settings, metadata = source.settings, source.metadata
    origin_z = float(metadata["local_origin"]["elevation_m"])
    height = settings.default_receiver_height_m
    with rasterio.open(settings.paths.raw / "terrain" / "dtm.tif") as terrain:
        transform = Transformer.from_crs("EPSG:4326", terrain.crs, always_xy=True)
        points = [transform.transform(u["longitude"], u["latitude"]) for u in ues]
        elevations = [float(v[0]) for v in terrain.sample(points)]
        invalid = [
            not math.isfinite(z) or (terrain.nodata is not None and z == terrain.nodata)
            for z in elevations
        ]
    fallbacks = []
    tile_size = float(settings.raw["area"]["tile_size_m"])
    half = math.ceil(float(source.manifest["width_m"]) / tile_size) * tile_size / 2
    for index, (ue, missing) in enumerate(zip(ues, invalid, strict=True)):
        if not missing:
            continue
        row = math.floor((ue["y_m"] + half) / tile_size)
        column = math.floor((ue["x_m"] + half) / tile_size)
        surface = source.run_dir / "surfaces" / f"r{row:03d}-c{column:03d}.npz"
        if not surface.exists():
            raise ValueError(
                f"UE {ue['ue_id']} terrain sample is nodata and its saved receiver surface "
                "is unavailable; no height or placement substitution made"
            )
        with np.load(surface, allow_pickle=False) as saved:
            x, y, z = saved["x_m"], saved["y_m"], saved["receiver_z_m"]
            cell = float(source.manifest["cell_size_m"])
            ix = math.floor((ue["x_m"] - float(x[0]) + cell / 2) / cell)
            iy = math.floor((ue["y_m"] - float(y[0]) + cell / 2) / cell)
            if not (0 <= ix < len(x) and 0 <= iy < len(y)):
                raise ValueError(f"Saved receiver surface does not contain UE {ue['ue_id']}")
            receiver_z = float(z[iy, ix])
        if not math.isfinite(receiver_z):
            raise ValueError(f"Saved receiver surface height is invalid for UE {ue['ue_id']}")
        portable = settings.portable_path(surface)
        source.sources[portable] = sha256_file(surface)
        elevations[index] = receiver_z + origin_z - height
        ue.update(
            terrain_height_source="saved-receiver-surface",
            terrain_nodata=True,
            terrain_height_surface=portable,
        )
        fallbacks.append(
            {
                "ue_id": ue["ue_id"],
                "reason": "native DTM sample is nodata",
                "source": portable,
                "receiver_z_local_m": receiver_z,
                "limitation": "Saved RF surface height is imputed or interpolated; not a measured terrain height.",
            }
        )
    return elevations, fallbacks


def trace_ue_channels(source, ues: list[dict]) -> tuple[dict, dict]:
    import mitsuba as mi
    from sionna.rt import PathSolver, PlanarArray, Receiver, Transmitter, load_scene
    from sionna.rt.radio_materials.itu import ITU_MATERIALS_PROPERTIES, itu_material

    from ottawa_rt.network import _hash

    started = time.perf_counter()
    scenario, settings, metadata = source.scenario, source.settings, source.metadata
    scene_xml = settings.resolve_path(source.manifest["scene_xml"])
    sector_path = settings.paths.processed / "sectors.jsonl"
    sectors = [
        s
        for s in load_sectors(sector_path)
        if s.sector_id in source.manifest["sector_ids"]
        and frequency_bucket_mhz(s.tx_frequency_mhz) == frequency_bucket_mhz(scenario.frequency_mhz)
    ]
    if not sectors:
        raise ValueError("No normalized transmitter sectors for the requested run and frequency")
    sources = [scene_xml, sector_path, settings.paths.raw / "terrain" / "dtm.tif"]
    for entry in ElementTree.parse(scene_xml).findall(".//string[@name='filename']"):
        sources.append(scene_xml.parent / entry.attrib["value"])
    source.sources.update({settings.portable_path(p): _hash(p) for p in sources})
    scene = load_scene(str(scene_xml), merge_shapes=True)
    fallbacks = []
    frequency = frequency_bucket_mhz(scenario.frequency_mhz) * 1e6
    for name, material in scene.radio_materials.items():
        itu_type = getattr(material, "itu_type", None)
        if itu_type not in ITU_MATERIALS_PROPERTIES:
            continue
        reference = nearest_supported_frequency_hz(
            list(ITU_MATERIALS_PROPERTIES[itu_type]), frequency
        )
        if reference is not None:
            material.frequency_update_callback = lambda _f, kind=itu_type, fixed=reference: (
                itu_material(kind, fixed)
            )
            fallbacks.append({"material": name, "reference_frequency_mhz": reference / 1e6})
    scene.frequency = frequency
    scene.bandwidth = scenario.bandwidth_mhz * 1e6
    scene.tx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    scene.rx_array = PlanarArray(num_rows=1, num_cols=1, pattern="iso", polarization="V")
    tx_positions = []
    for i, sector in enumerate(sectors):
        x, y = _local_xy(metadata, sector.latitude, sector.longitude)
        z = _terrain_height_local(settings, metadata, sector) + sector.antenna_height_agl_m
        tx_positions.append((x, y, z))
    elevations, terrain_fallbacks = _ue_terrain_elevations(source, ues)
    height = settings.default_receiver_height_m
    for i, (ue, elevation) in enumerate(zip(ues, elevations, strict=True)):
        z = elevation - metadata["local_origin"]["elevation_m"] + height
        ue.update(
            height_agl_m=height,
            z_local_m=z,
            elevation_m=elevation + height,
            rf_channel_source="direct-ue-cir",
        )
        scene.add(Receiver(f"ue-{i:05d}", position=[ue["x_m"], ue["y_m"], z]))
    flags = source.manifest.get("simulation_parameters", settings.raw["simulation"])
    solver_flags = {
        k: bool(flags.get(k, False))
        for k in (
            "los",
            "specular_reflection",
            "diffuse_reflection",
            "refraction",
            "diffraction",
            "edge_diffraction",
        )
    }
    # A single transmitter per trace keeps working memory inside an 8 GB GPU.
    # The scene remains loaded; CIRs are evaluated before moving to the next TX.
    channel_parts = []
    solver = PathSolver(deterministic=True)
    for i, position in enumerate(tx_positions):
        name = f"tx-{i:05d}"
        scene.add(Transmitter(name, position=list(position), power_dbm=0))
        paths = solver(
            scene,
            max_depth=scenario.ray_max_depth,
            samples_per_src=scenario.ray_samples_per_src,
            max_num_paths_per_src=10000,
            seed=scenario.seed + i,
            **solver_flags,
        )
        channel_parts.append(paths.cir(normalize_delays=False, out_type="numpy"))
        scene.remove(name)
    max_paths = max(part[0].shape[-2] for part in channel_parts)
    padded_a, padded_tau = [], []
    for part_a, part_tau in channel_parts:
        missing = max_paths - part_a.shape[-2]
        padded_a.append(np.pad(part_a, [(0, 0)] * 4 + [(0, missing), (0, 0)]))
        padded_tau.append(np.pad(part_tau, [(0, 0)] * 2 + [(0, missing)], constant_values=-1))
    a = np.concatenate(padded_a, axis=2)
    tau = np.concatenate(padded_tau, axis=1)
    # SISO CIR: [UE, 1, sector, 1, path, time]. Sum path energies.
    amplitudes = a[:, 0, :, 0, :, 0]
    gain = np.sum(np.abs(amplitudes) ** 2, axis=-1).T
    rss = np.full_like(gain, -np.inf, dtype=np.float64)
    reciprocal_gain_db = np.full_like(rss, -np.inf)
    x = np.asarray([u["x_m"] for u in ues])
    y = np.asarray([u["y_m"] for u in ues])
    z = np.asarray([u["z_local_m"] for u in ues])
    from ottawa_rt.calibrated_coverage import offset_db

    for i, sector in enumerate(sectors):
        with np.errstate(divide="ignore"):
            path_gain = 10 * np.log10(gain[i])
        pattern = sector_pattern_gain_db(
            sector,
            x,
            y,
            z,
            tx_positions[i],
            float(settings.raw["antenna_defaults"].get("front_to_back_db", 30)),
        )
        rss[i] = path_gain + sector.tx_power_dbm - sector.line_loss_db + pattern
        reciprocal_gain_db[i] = path_gain - sector.line_loss_db + pattern
        if source.calibration is not None:
            rss[i] += offset_db(source.calibration, sector.sector_id, sector.tx_frequency_mhz)
    eligible = np.asarray(
        [not scenario.operator or scenario.operator.lower() in s.operator.lower() for s in sectors]
    )
    noise = 10 ** (thermal_noise_dbm(scenario.bandwidth_mhz, scenario.noise_figure_db) / 10)
    for i, ue in enumerate(ues):
        candidates = np.flatnonzero(eligible & np.isfinite(rss[:, i]))
        if not len(candidates):
            ue.update(
                serving_sector_id=None,
                sinr_db=None,
                rsrp_dbm=None,
                received_power_dbm=None,
                operator=None,
                path_count=0,
            )
            continue
        server = candidates[np.argmax(rss[candidates, i])]
        power = 10 ** (rss[:, i] / 10)
        interference = np.sum(np.delete(power, server))
        valid_paths = np.abs(amplitudes[i, server]) > 0
        delays = tau[i, server][valid_paths]
        ue.update(
            serving_sector_id=sectors[server].sector_id,
            operator=sectors[server].operator,
            received_power_dbm=float(rss[server, i]),
            rsrp_dbm=float(rss[server, i] - 10 * np.log10(scenario.num_rb * 12)),
            sinr_db=float(rss[server, i] - 10 * np.log10(interference + noise)),
            path_count=int(valid_paths.sum()),
            propagation_delay_us=float(np.min(delays) * 1e6) if len(delays) else None,
        )
        if scenario.direction == "uplink":
            ue.update(
                association_downlink_received_power_dbm=float(rss[server, i]),
                association_downlink_rsrp_dbm=ue["rsrp_dbm"],
                received_power_dbm=float(reciprocal_gain_db[server, i] + scenario.ue_tx_power_dbm),
                sinr_db=None,  # actual UL SINR depends on simultaneous grants
            )
    trace = {
        "source": "direct-ue-cir",
        "mitsuba_variant": mi.variant(),
        "samples_per_src": scenario.ray_samples_per_src,
        "max_depth": scenario.ray_max_depth,
        "frequency_mhz": frequency / 1e6,
        "flags": solver_flags,
        "sector_count": len(sectors),
        "material_frequency_fallbacks": fallbacks,
        "terrain_height_fallbacks": terrain_fallbacks,
        "transmitters_per_trace": 1,
        "max_num_paths_per_src": 10000,
        "wall_time_s": time.perf_counter() - started,
        "power_reduction": "sum of SISO CIR path energies; directional pattern applied per link",
        "uplink_reciprocity": scenario.direction == "uplink",
    }
    channel_data = {
        "a": a,
        "tau": tau,
        "rss_dbm": rss,
        "reciprocal_gain_db": reciprocal_gain_db,
        "sector_ids": np.asarray([s.sector_id for s in sectors]),
        "ue_ids": np.asarray([u["ue_id"] for u in ues]),
        "trace_metadata": np.asarray(json.dumps(trace)),
    }
    return trace, channel_data
