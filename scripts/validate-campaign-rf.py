"""Real 1 m receiver-grid/batched RF preflight on the existing Ottawa scene."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ottawa_rt.config import load_settings
from ottawa_rt.jobs import SimulationJob
from ottawa_rt.simulation import _build_measurement_surface, execute_job
from ottawa_rt.tiling import Tile

settings = load_settings("config/demo-6km-2p5m.yaml")
settings.raw["area"]["cell_size_m"] = 1
settings.raw["simulation"]["tx_batch_size"] = 4
run = settings.paths.runs / "measurement-validation-6km-2p5m-20260925"
source = run / "queue/done/r005-c006-2120p0MHz.json"
payload = json.loads(source.read_text(encoding="utf-8"))
fields = SimulationJob.__dataclass_fields__
job = SimulationJob(**{k: v for k, v in payload.items() if k in fields})
destination = Path(".tmp/campaign-rf-smoke")
destination.mkdir(parents=True, exist_ok=True)
surface = destination / "r005-c006-surface.ply"
metadata = json.loads(
    settings.resolve_path(job.scene_xml)
    .with_name("scene_metadata.json")
    .read_text(encoding="utf-8")
)
if not surface.exists():
    print("Building 600 by 600 1 m terrain-relative receiver cells", flush=True)
    _build_measurement_surface(settings, metadata, Tile(**job.tile), surface)
job = replace(
    job, output_path=(destination / "tile.npz").as_posix(), measurement_surface=surface.as_posix()
)
print("Tracing production ray budget with four-transmitter GPU batches", flush=True)
metrics = execute_job(settings, job)
with np.load(destination / "tile.npz") as data:
    assert data["rss_dbm"].shape[1:] == (600, 600)
    assert float(data["cell_size_m"]) == 1
    finite = np.isfinite(data["rss_dbm"])
    assert finite.any()
    assert metrics["tx_batch_size"] == 4
    metrics["finite_sector_receiver_cells"] = int(finite.sum())
    metrics["receiver_cells_with_signal"] = int(finite.any(axis=0).sum())
output = Path("outputs/campaign-rf-preflight.json")
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
print(json.dumps(metrics, indent=2), flush=True)
