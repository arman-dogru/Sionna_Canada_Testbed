from __future__ import annotations

import json
import math
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from ottawa_rt.config import Settings
from ottawa_rt.geo import BoundingBox, bbox_from_center

TARGET_CRS = "EPSG:26918"  # NAD83 / UTM zone 18N


@dataclass(frozen=True)
class SceneBuildResult:
    scene_dir: Path
    scene_xml: Path
    metadata: Path
    building_count: int
    triangle_count: int


def _require_geo():
    try:
        import geopandas as gpd
        import rasterio
        import trimesh
        from pyproj import Transformer
        from shapely.geometry import Polygon
        from shapely.ops import triangulate
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("Scene building requires the 'geo' dependency group") from exc
    return gpd, rasterio, trimesh, Transformer, Polygon, triangulate


def _extract_geodatabases(archives: list[Path], destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    geodatabases: list[Path] = []
    for archive in archives:
        target = destination / archive.stem.replace(".gdb", "")
        if not target.exists():
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(target)
        candidates = list(target.rglob("*.gdb"))
        if candidates:
            geodatabases.extend(candidates)
        else:
            # Some ArcGIS downloads contain the .gdb contents at archive root.
            geodatabases.append(target)
    return geodatabases


def _read_buildings(geodatabases: list[Path], bbox: BoundingBox):
    gpd, _, _, Transformer, _, _ = _require_geo()
    import fiona
    import pandas as pd
    import shapely
    from shapely.geometry import shape

    frames = []
    for geodatabase in geodatabases:
        try:
            layers = fiona.listlayers(geodatabase)
        except (OSError, ValueError, RuntimeError, fiona.errors.FionaError):
            continue
        for layer_name in layers:
            try:
                source = fiona.open(geodatabase, layer=layer_name)
            except (OSError, ValueError, RuntimeError, fiona.errors.FionaError):
                continue
            with source:
                source_crs = source.crs_wkt or source.crs
                to_source = Transformer.from_crs("EPSG:4326", source_crs, always_xy=True)
                corners = [
                    to_source.transform(bbox.west, bbox.south),
                    to_source.transform(bbox.west, bbox.north),
                    to_source.transform(bbox.east, bbox.south),
                    to_source.transform(bbox.east, bbox.north),
                ]
                source_bbox = (
                    min(point[0] for point in corners),
                    min(point[1] for point in corners),
                    max(point[0] for point in corners),
                    max(point[1] for point in corners),
                )
                records = []
                for feature in source.filter(bbox=source_bbox):
                    if feature["geometry"] is None:
                        continue
                    geometry = shapely.force_2d(shape(feature["geometry"]))
                    # Ottawa distributes the LOD1 models as Esri MultiPatch
                    # solids. Fiona exposes their faces as a MultiPolygon. The
                    # union of non-degenerate XY faces is the original building
                    # footprint, while HGT_LIDAR retains the authoritative height.
                    parts = (
                        list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
                    )
                    surfaces = [
                        part for part in parts if part.geom_type == "Polygon" and part.area > 0.01
                    ]
                    if not surfaces:
                        continue
                    footprint = shapely.make_valid(shapely.union_all(surfaces))
                    polygons = (
                        [footprint]
                        if footprint.geom_type == "Polygon"
                        else [part for part in footprint.geoms if part.geom_type == "Polygon"]
                    )
                    for polygon in polygons:
                        if polygon.area < 2.0:
                            continue
                        record = dict(feature["properties"])
                        record["geometry"] = polygon
                        records.append(record)
                if records:
                    frame = gpd.GeoDataFrame(records, geometry="geometry", crs=source_crs).to_crs(
                        TARGET_CRS
                    )
                    frames.append(frame)
    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=TARGET_CRS
    )


def _polygon_height(row: object, default_height: float = 10.0) -> float:
    candidate_names = (
        "height",
        "HEIGHT",
        "Height",
        "MAX_HEIGHT",
        "max_height",
        "BLDGHEIGHT",
        "roof_height",
        "HGT_LIDAR",
        "ELEV_TOP",
    )
    for name in candidate_names:
        try:
            value = float(row[name])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and 1.0 <= value <= 500.0:
            if name == "ELEV_TOP":
                try:
                    bottom = float(row["ELEV_BOTTOM"])
                    if math.isfinite(bottom) and 1.0 <= value - bottom <= 500.0:
                        return value - bottom
                except (KeyError, TypeError, ValueError):
                    continue
            return value
    geometry = row.geometry
    try:
        z_values = [
            coordinate[2] for coordinate in geometry.exterior.coords if len(coordinate) >= 3
        ]
        if z_values and max(z_values) - min(z_values) >= 1.0:
            return max(z_values) - min(z_values)
    except (AttributeError, IndexError):
        pass
    return default_height


def _sample_terrain(dataset: object, x: float, y: float) -> float:
    try:
        value = next(dataset.sample([(x, y)]))[0]
        if np.isfinite(value) and value != dataset.nodata:
            return float(value)
    except (StopIteration, ValueError, IndexError, TypeError):
        return 0.0
    return 0.0


def _extrude_polygon_mesh(
    polygon: object, base_z: float, height: float, origin: tuple[float, float, float]
):
    _, _, trimesh, _, _, triangulate = _require_geo()
    if polygon.is_empty or polygon.area < 2.0:
        return None
    polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    ox, oy, oz = origin
    ring = list(polygon.exterior.coords)[:-1]
    if len(ring) < 3:
        return None
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    vertex_index: dict[tuple[float, float, float], int] = {}

    def add_vertex(x: float, y: float, z: float) -> int:
        key = (round(x - ox, 4), round(y - oy, 4), round(z - oz, 4))
        if key not in vertex_index:
            vertex_index[key] = len(vertices)
            vertices.append(key)
        return vertex_index[key]

    top_z = base_z + height
    for triangle in triangulate(polygon):
        if not polygon.covers(triangle.representative_point()):
            continue
        points = list(triangle.exterior.coords)[:3]
        top = tuple(add_vertex(x, y, top_z) for x, y, *_ in points)
        bottom = tuple(add_vertex(x, y, base_z) for x, y, *_ in points)
        faces.append(top)
        faces.append((bottom[2], bottom[1], bottom[0]))
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        b0, b1 = add_vertex(start[0], start[1], base_z), add_vertex(end[0], end[1], base_z)
        t0, t1 = add_vertex(start[0], start[1], top_z), add_vertex(end[0], end[1], top_z)
        faces.extend([(b0, b1, t1), (b0, t1, t0)])
    if not faces:
        return None
    return trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=False)


def _terrain_mesh(
    dtm_path: Path, destination: Path, origin: tuple[float, float, float], spacing_m: float = 10.0
):
    _, rasterio, trimesh, _, _, _ = _require_geo()
    from rasterio.enums import Resampling
    from rasterio.warp import calculate_default_transform, reproject

    with rasterio.open(dtm_path) as source:
        transform, width, height = calculate_default_transform(
            source.crs,
            TARGET_CRS,
            source.width,
            source.height,
            *source.bounds,
            resolution=spacing_m,
        )
        data = np.full((height, width), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(source, 1),
            destination=data,
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=transform,
            dst_crs=TARGET_CRS,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
    rows, cols = np.indices(data.shape)
    xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")
    xs, ys = np.asarray(xs), np.asarray(ys)
    finite = np.isfinite(data)
    fill = float(np.nanmedian(data)) if finite.any() else 0.0
    data = np.nan_to_num(data, nan=fill)
    ox, oy, oz = origin
    vertices = np.column_stack(((xs - ox).ravel(), (ys - oy).ravel(), (data - oz).ravel()))
    faces = []
    for row in range(height - 1):
        for col in range(width - 1):
            i = row * width + col
            faces.extend([(i, i + 1, i + width + 1), (i, i + width + 1, i + width)])
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)
    mesh.export(destination)
    return mesh, transform, data


def _write_scene_xml(destination: Path, profiles: dict[str, dict[str, object]]) -> None:
    scene = ET.Element("scene", {"version": "2.1.0"})
    for name, profile in profiles.items():
        bsdf = ET.SubElement(scene, "bsdf", {"type": "itu-radio-material", "id": f"mat-{name}"})
        ET.SubElement(bsdf, "string", {"name": "type", "value": str(profile["itu_type"])})
        ET.SubElement(
            bsdf, "float", {"name": "thickness", "value": str(profile.get("thickness_m", 0.1))}
        )
        ET.SubElement(
            bsdf,
            "float",
            {
                "name": "scattering_coefficient",
                "value": str(profile.get("scattering_coefficient", 0.0)),
            },
        )
    for shape_id, filename, material_id in (
        ("terrain-geometry", "meshes/terrain.ply", "mat-ground"),
        ("building-geometry", "meshes/buildings.ply", "mat-concrete"),
    ):
        shape = ET.SubElement(scene, "shape", {"type": "ply", "id": shape_id})
        ET.SubElement(shape, "string", {"name": "filename", "value": filename})
        ET.SubElement(shape, "boolean", {"name": "face_normals", "value": "true"})
        ET.SubElement(shape, "ref", {"id": material_id, "name": "bsdf"})
    ET.indent(scene, space="  ")
    ET.ElementTree(scene).write(destination, encoding="utf-8", xml_declaration=True)


def build_scene(settings: Settings, width_m: float, *, force: bool = False) -> SceneBuildResult:
    _, rasterio, trimesh, Transformer, _, _ = _require_geo()
    scene_name = f"legget-{int(width_m)}m"
    scene_dir = settings.paths.scenes / scene_name
    scene_xml = scene_dir / "scene.xml"
    metadata_path = scene_dir / "scene_metadata.json"
    if scene_xml.exists() and metadata_path.exists() and not force:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return SceneBuildResult(
            scene_dir,
            scene_xml,
            metadata_path,
            metadata["building_count"],
            metadata["triangle_count"],
        )

    full_width = width_m + 2 * float(settings.raw["area"]["context_m"])
    bbox = bbox_from_center(*settings.anchor, full_width)
    archives = sorted((settings.paths.raw / "ottawa_lod1").glob("*.gdb.zip"))
    dtm_path = settings.paths.raw / "terrain" / "dtm.tif"
    if not archives or not dtm_path.exists():
        raise FileNotFoundError("Run `ottawa-rt fetch-data` before building a scene")

    if force and scene_dir.exists():
        shutil.rmtree(scene_dir)
    mesh_dir = scene_dir / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    transformer = Transformer.from_crs("EPSG:4326", TARGET_CRS, always_xy=True)
    origin_x, origin_y = transformer.transform(settings.anchor[1], settings.anchor[0])
    with rasterio.open(dtm_path) as dtm:
        terrain_transformer = Transformer.from_crs(TARGET_CRS, dtm.crs, always_xy=True)
        sample_x, sample_y = terrain_transformer.transform(origin_x, origin_y)
        origin_z = _sample_terrain(dtm, sample_x, sample_y)
    origin = (origin_x, origin_y, origin_z)

    terrain, _, _ = _terrain_mesh(dtm_path, mesh_dir / "terrain.ply", origin)
    geodatabases = _extract_geodatabases(archives, settings.paths.processed / "gdb")
    buildings = _read_buildings(geodatabases, bbox)
    if not buildings.empty:
        import shapely

        preview = buildings[["geometry"]].copy()
        preview["height_m"] = [_polygon_height(row) for _, row in buildings.iterrows()]
        preview.geometry = preview.geometry.apply(shapely.force_2d)
        preview.to_crs("EPSG:4326").to_file(scene_dir / "buildings.geojson", driver="GeoJSON")
    meshes = []
    with rasterio.open(dtm_path) as dtm:
        to_dtm = Transformer.from_crs(TARGET_CRS, dtm.crs, always_xy=True)
        for _, row in buildings.iterrows():
            geometry = row.geometry
            parts = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
            for polygon in parts:
                centroid = polygon.centroid
                sx, sy = to_dtm.transform(centroid.x, centroid.y)
                base = _sample_terrain(dtm, sx, sy)
                mesh = _extrude_polygon_mesh(polygon, base, _polygon_height(row), origin)
                if mesh is not None:
                    meshes.append(mesh)
    if meshes:
        building_mesh = trimesh.util.concatenate(meshes)
    else:
        building_mesh = trimesh.Trimesh(
            vertices=np.asarray([[0, 0, -100], [0.01, 0, -100], [0, 0.01, -100]]),
            faces=np.asarray([[0, 1, 2]]),
            process=False,
        )
    building_mesh.export(mesh_dir / "buildings.ply")
    profiles = settings.raw["materials"]
    _write_scene_xml(scene_xml, profiles)
    metadata = {
        "schema_version": 1,
        "name": scene_name,
        "anchor_wgs84": {"latitude": settings.anchor[0], "longitude": settings.anchor[1]},
        "target_crs": TARGET_CRS,
        "local_origin": {"easting": origin_x, "northing": origin_y, "elevation_m": origin_z},
        "output_width_m": width_m,
        "context_m": settings.raw["area"]["context_m"],
        "bbox_wgs84": bbox.as_list(),
        "building_count": len(meshes),
        "terrain_triangle_count": len(terrain.faces),
        "building_triangle_count": len(building_mesh.faces),
        "triangle_count": int(len(terrain.faces) + len(building_mesh.faces)),
        "building_height_fallback_m": 10.0,
        "material_profiles": profiles,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return SceneBuildResult(
        scene_dir,
        scene_xml,
        metadata_path,
        len(meshes),
        int(metadata["triangle_count"]),
    )
