from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from ottawa_rt.config import Settings
from ottawa_rt.data.provenance import ProvenanceManifest
from ottawa_rt.geo import BoundingBox, bbox_from_center

USER_AGENT = "OttawaSionnaRT/0.1 (public research data pipeline)"


def download_file(url: str, destination: Path, *, force: bool = False) -> Path:
    if destination.exists() and destination.stat().st_size > 0 and not force:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    headers = {"User-Agent": USER_AGENT}
    with httpx.stream(
        "GET", url, headers=headers, follow_redirects=True, timeout=180.0
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                handle.write(chunk)
    partial.replace(destination)
    return destination


def _query_ottawa_lod1(index_url: str, bbox: BoundingBox) -> list[dict[str, object]]:
    params = {
        "where": "1=1",
        "outFields": "FID,ONS_ID,ONS_Name,ONS_Region,GDB",
        "geometry": ",".join(map(str, [bbox.west, bbox.south, bbox.east, bbox.north])),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "f": "json",
    }
    response = httpx.get(
        f"{index_url}/query", params=params, headers={"User-Agent": USER_AGENT}, timeout=60
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(f"Ottawa LOD1 query failed: {payload['error']}")
    return [feature["attributes"] for feature in payload.get("features", [])]


def _resolve_arcgis_item_id(short_url: str) -> str:
    if not short_url.startswith("http"):
        raise ValueError(f"Not a downloadable ArcGIS URL: {short_url}")
    response = httpx.get(
        short_url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30
    )
    response.raise_for_status()
    query = parse_qs(urlparse(str(response.url)).query)
    item_id = query.get("id", [None])[0]
    if not item_id:
        match = re.search(r"[?&]id=([0-9a-f]{32})", str(response.url), flags=re.IGNORECASE)
        item_id = match.group(1) if match else None
    if not item_id:
        raise ValueError(f"Could not resolve ArcGIS item id from {short_url} -> {response.url}")
    return item_id


def fetch_ised(settings: Settings, manifest: ProvenanceManifest, *, force: bool = False) -> Path:
    url = settings.raw["sources"]["ised_terrestrial_zip"]
    path = settings.paths.raw / "ised" / "Site_Data_Extract_FX.zip"
    download_file(url, path, force=force)
    manifest.add_file(
        name="ised-terrestrial-site-extract",
        source_url=url,
        path=path,
        licence="Open Government Licence - Canada",
        metadata={
            "contains_public_contact_fields": True,
            "normalizer_retains_contact_fields": False,
        },
    )
    return path


def fetch_ottawa_buildings(
    settings: Settings,
    manifest: ProvenanceManifest,
    bbox: BoundingBox,
    *,
    force: bool = False,
) -> list[Path]:
    index_url = settings.raw["sources"]["ottawa_lod1_index"]
    records = _query_ottawa_lod1(index_url, bbox)
    index_path = settings.paths.raw / "ottawa_lod1" / "selected_neighbourhoods.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    manifest.add_file(
        name="ottawa-lod1-selected-index",
        source_url=index_url,
        path=index_path,
        licence="Open Government Licence - City of Ottawa",
        metadata={"bbox_wgs84": bbox.as_list(), "record_count": len(records)},
    )

    downloads: list[Path] = []
    for record in records:
        gdb_url = str(record.get("GDB") or "")
        if not gdb_url.startswith("http"):
            continue
        item_id = _resolve_arcgis_item_id(gdb_url)
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(record.get("ONS_Name") or item_id)).strip("_")
        destination = settings.paths.raw / "ottawa_lod1" / f"{name}.gdb.zip"
        data_url = f"https://www.arcgis.com/sharing/rest/content/items/{item_id}/data"
        download_file(data_url, destination, force=force)
        manifest.add_file(
            name=f"ottawa-lod1-{name.lower()}",
            source_url=data_url,
            path=destination,
            licence="Open Government Licence - City of Ottawa",
            metadata={"arcgis_item_id": item_id, "neighbourhood": record.get("ONS_Name")},
        )
        downloads.append(destination)
    return downloads


def _search_hrdem(stac_url: str, bbox: BoundingBox) -> list[dict[str, object]]:
    params = {
        "collections": "hrdem-lidar",
        "bbox": ",".join(map(str, bbox.as_list())),
        "limit": 100,
    }
    response = httpx.get(stac_url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    return response.json().get("features", [])


def _choose_hrdem(features: list[dict[str, object]]) -> dict[str, object]:
    if not features:
        raise RuntimeError("No HRDEM LiDAR project intersects the requested area")
    preferred = [item for item in features if "OTTAWA" in str(item.get("id", "")).upper()]
    candidates = preferred or features
    return max(candidates, key=lambda item: str(item.get("properties", {}).get("datetime", "")))


def fetch_hrdem_clip(
    settings: Settings,
    manifest: ProvenanceManifest,
    bbox: BoundingBox,
    *,
    force: bool = False,
) -> Path:
    """Window a public HRDEM cloud-optimized DTM to the requested WGS84 bounds."""
    destination = settings.paths.raw / "terrain" / "dtm.tif"
    if destination.exists() and not force:
        return destination
    try:
        import rasterio
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("Terrain acquisition requires the 'geo' dependency group") from exc

    stac_url = settings.raw["sources"]["hrdem_stac_search"]
    feature = _choose_hrdem(_search_hrdem(stac_url, bbox))
    source_url = feature["assets"]["dtm"]["href"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES"), rasterio.open(source_url) as source:
        left, bottom, right, top = transform_bounds(
            "EPSG:4326", source.crs, bbox.west, bbox.south, bbox.east, bbox.north
        )
        window = (
            from_bounds(left, bottom, right, top, source.transform).round_offsets().round_lengths()
        )
        data = source.read(1, window=window, boundless=True, fill_value=source.nodata)
        profile = source.profile.copy()
        profile.update(
            height=data.shape[0],
            width=data.shape[1],
            transform=source.window_transform(window),
            compress="deflate",
            tiled=True,
        )
        with rasterio.open(destination, "w", **profile) as target:
            target.write(data, 1)
    manifest.add_file(
        name="nrcan-hrdem-dtm-clip",
        source_url=source_url,
        path=destination,
        licence="Open Government Licence - Canada",
        metadata={"stac_item": feature.get("id"), "bbox_wgs84": bbox.as_list()},
    )
    return destination


def fetch_all(
    settings: Settings, *, width_m: float | None = None, force: bool = False
) -> dict[str, object]:
    settings.paths.ensure()
    manifest = ProvenanceManifest(settings.paths.raw / "provenance.json", settings.paths.root)
    area = settings.raw["area"]
    requested_width = float(width_m or area["min_width_m"]) + 2 * float(area["context_m"])
    bbox = bbox_from_center(*settings.anchor, requested_width)
    result = {
        "bbox_wgs84": bbox.as_list(),
        "ised": str(fetch_ised(settings, manifest, force=force)),
        "buildings": [
            str(path) for path in fetch_ottawa_buildings(settings, manifest, bbox, force=force)
        ],
        "terrain": str(fetch_hrdem_clip(settings, manifest, bbox, force=force)),
        "manifest": str(manifest.path),
    }
    manifest.make_portable()
    return result
