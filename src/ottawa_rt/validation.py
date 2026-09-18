from __future__ import annotations

import json

from ottawa_rt.config import Settings
from ottawa_rt.data.ised import load_sectors
from ottawa_rt.data.provenance import ProvenanceManifest, sha256_file


def validate_project(settings: Settings) -> dict[str, object]:
    checks: list[dict[str, object]] = []

    def add(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    manifest_path = settings.paths.raw / "provenance.json"
    manifest = ProvenanceManifest(manifest_path, settings.paths.root)
    entries = manifest.read()
    add("provenance_exists", bool(entries), str(manifest_path))
    for entry in entries:
        path = manifest.resolve_local_path(entry.local_path)
        valid = path.exists() and sha256_file(path) == entry.sha256
        add(f"checksum:{entry.name}", valid, str(path))

    sector_path = settings.paths.processed / "sectors.jsonl"
    sectors = load_sectors(sector_path)
    add("normalized_sectors", bool(sectors), len(sectors))
    invalid_eirp = [item.sector_id for item in sectors if not -50 <= item.eirp_dbm <= 120]
    add("sector_eirp_range", not invalid_eirp, invalid_eirp[:20])

    scenes = list(settings.paths.scenes.glob("*/scene.xml"))
    add("scene_exists", bool(scenes), [str(path) for path in scenes])
    for scene in scenes:
        metadata = scene.parent / "scene_metadata.json"
        meshes = [
            scene.parent / "meshes" / "terrain.ply",
            scene.parent / "meshes" / "buildings.ply",
        ]
        add(
            f"scene_complete:{scene.parent.name}",
            metadata.exists() and all(path.exists() for path in meshes),
            str(scene),
        )

    run_reports = []
    for path in settings.paths.runs.glob("*/run.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_reports.append(
            {
                "run_id": payload.get("run_id"),
                "status": payload.get("status"),
                "queue": payload.get("queue"),
            }
        )
    add("runs_readable", True, run_reports)
    return {"passed": all(item["passed"] for item in checks), "checks": checks}
