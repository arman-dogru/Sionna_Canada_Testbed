from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from ottawa_rt.config import Settings
from ottawa_rt.jobs import FileJobQueue
from ottawa_rt.stitch import stitch_run
from ottawa_rt.visualization import render_run


def finalize_run(
    settings: Settings,
    run_id: str,
    *,
    wait: bool = False,
    poll_seconds: float = 30.0,
) -> dict[str, object]:
    """Wait for a queue if requested, then stitch it and create presentation figures."""
    run_dir = settings.paths.runs / run_id
    queue = FileJobQueue(run_dir / "queue")
    counts = queue.counts()
    while wait and (counts["pending"] or counts["processing"]):
        time.sleep(max(poll_seconds, 1.0))
        counts = queue.counts()

    stitch = stitch_run(settings, run_id)
    try:
        visualization = render_run(settings, run_id)
    except FileNotFoundError as exc:
        visualization = {"outputs": [], "warning": str(exc)}
    complete_groups = [
        item for item in stitch["frequency_groups"] if item.get("status") == "complete"
    ]
    status = (
        "complete"
        if counts["failed"] == 0 and len(complete_groups) == len(stitch["frequency_groups"])
        else "partial"
    )
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "queue": counts,
        "stitch": stitch,
        "visualization": visualization,
    }
    (run_dir / "finalization.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    manifest_path = run_dir / "run.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = status
    manifest["queue"] = counts
    manifest["finalized_at"] = report["generated_at"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report
