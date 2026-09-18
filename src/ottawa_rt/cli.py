from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from ottawa_rt.benchmark import estimate_candidates
from ottawa_rt.calibration import build_baseline
from ottawa_rt.calibration import calibrate as fit_calibration
from ottawa_rt.calibration import import_measurements as import_rows
from ottawa_rt.config import Settings, load_settings
from ottawa_rt.data.fetch import fetch_all
from ottawa_rt.data.ised import normalize_ised
from ottawa_rt.finalize import finalize_run
from ottawa_rt.geo import bbox_from_center
from ottawa_rt.jobs import FileJobQueue
from ottawa_rt.query import PredictionStore
from ottawa_rt.scene import build_scene as create_scene
from ottawa_rt.simulation import prepare_run
from ottawa_rt.simulation import worker as run_worker
from ottawa_rt.stitch import stitch_run
from ottawa_rt.validation import validate_project
from ottawa_rt.visualization import render_run

app = typer.Typer(no_args_is_help=True, help="Ottawa Sionna RT cellular digital-twin toolkit")


def _settings(config: Path) -> Settings:
    settings = load_settings(config)
    settings.paths.ensure()
    return settings


def _print(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


ConfigOption = Annotated[Path, typer.Option("--config", "-c", help="Project YAML configuration")]


@app.command("fetch-data")
def fetch_data(
    config: ConfigOption = Path("config/default.yaml"),
    width_m: Annotated[
        float | None, typer.Option(help="Output width before context buffer")
    ] = None,
    force: Annotated[bool, typer.Option(help="Refresh existing snapshots")] = False,
) -> None:
    """Snapshot ISED, Ottawa LOD1, and clipped NRCan HRDEM public data."""
    _print(fetch_all(_settings(config), width_m=width_m, force=force))


@app.command("normalize-ised")
def normalize_ised_command(
    config: ConfigOption = Path("config/default.yaml"),
    width_m: Annotated[
        float | None, typer.Option(help="Output width before context buffer")
    ] = None,
) -> None:
    settings = _settings(config)
    area = settings.raw["area"]
    total_width = float(width_m or area["max_width_m"]) + 2 * float(area["context_m"])
    summary = normalize_ised(
        settings.paths.raw / "ised" / "Site_Data_Extract_FX.zip",
        settings.paths.processed / "sectors.jsonl",
        bbox_from_center(*settings.anchor, total_width),
        {key: float(value) for key, value in settings.raw["antenna_defaults"].items()},
    )
    _print(summary)


@app.command("build-scene")
def build_scene_command(
    width_m: Annotated[float, typer.Option(help="Output region width in metres")],
    config: ConfigOption = Path("config/default.yaml"),
    force: Annotated[bool, typer.Option(help="Rebuild scene artifacts")] = False,
) -> None:
    result = create_scene(_settings(config), width_m, force=force)
    _print(result.__dict__)


@app.command()
def benchmark(
    config: ConfigOption = Path("config/default.yaml"),
    seconds_per_tile_band: Annotated[
        float, typer.Option(help="Measured smoke-run duration")
    ] = 120.0,
    scene_vram_gb: Annotated[
        float, typer.Option(help="Measured peak VRAM for the reference scene")
    ] = 2.0,
    measured_width_m: Annotated[float, typer.Option(help="Reference scene width")] = 2000.0,
    band_count: Annotated[int, typer.Option(help="Frequency groups discovered/expected")] = 4,
) -> None:
    _print(
        estimate_candidates(
            _settings(config),
            measured_seconds_per_tile_band=seconds_per_tile_band,
            measured_scene_vram_gb=scene_vram_gb,
            measured_width_m=measured_width_m,
            band_count=band_count,
        )
    )


@app.command()
def simulate(
    width_m: Annotated[float, typer.Option(help="Built scene/output width in metres")],
    config: ConfigOption = Path("config/default.yaml"),
    run_id: Annotated[str | None, typer.Option(help="Stable run identifier")] = None,
    frequency_mhz: Annotated[
        list[float] | None,
        typer.Option("--frequency-mhz", help="Repeat to enqueue selected 5 MHz frequency groups"),
    ] = None,
    execute: Annotated[
        bool, typer.Option(help="Launch one worker process per configured local GPU")
    ] = False,
) -> None:
    settings = _settings(config)
    manifest = prepare_run(
        settings,
        width_m=width_m,
        run_id=run_id,
        frequency_groups_mhz=frequency_mhz,
    )
    _print(manifest)
    if not execute:
        return
    processes = []
    for gpu in range(int(settings.raw["compute"]["local_gpu_count"])):
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = str(gpu)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ottawa_rt.cli",
                    "worker",
                    manifest["run_id"],
                    "--config",
                    str(config),
                ],
                env=environment,
            )
        )
    exit_codes = [process.wait() for process in processes]
    failures = [code for code in exit_codes if code != 0]
    if failures:
        raise typer.Exit(code=1)


@app.command()
def worker(
    run_id: str,
    config: ConfigOption = Path("config/default.yaml"),
    once: Annotated[bool, typer.Option(help="Execute at most one job")] = False,
    samples_per_tx: Annotated[
        int | None, typer.Option(help="Override ray samples for a smoke run")
    ] = None,
    max_depth: Annotated[
        int | None, typer.Option(help="Override path depth for a smoke run")
    ] = None,
) -> None:
    settings = _settings(config)
    if samples_per_tx is not None:
        settings.raw["simulation"]["samples_per_tx"] = samples_per_tx
    if max_depth is not None:
        settings.raw["simulation"]["max_depth"] = max_depth
    _print(run_worker(settings, run_id, once=once))


@app.command("retry-failed")
def retry_failed(
    run_id: str,
    config: ConfigOption = Path("config/default.yaml"),
) -> None:
    """Return failed jobs to the pending queue after correcting their cause."""
    settings = _settings(config)
    queue = FileJobQueue(settings.paths.runs / run_id / "queue")
    _print({"retried": queue.retry_failed(), "queue": queue.counts()})


@app.command()
def stitch(
    run_id: str,
    config: ConfigOption = Path("config/default.yaml"),
) -> None:
    """Stitch completed tile cores and report overlap seam error."""
    _print(stitch_run(_settings(config), run_id))


@app.command("render-run")
def render_run_command(
    run_id: str,
    config: ConfigOption = Path("config/default.yaml"),
    frequency_mhz: Annotated[
        float | None, typer.Option(help="Render only one stitched group")
    ] = None,
) -> None:
    """Create reference-style path-gain, RSS, and SINR presentation figures."""
    _print(render_run(_settings(config), run_id, frequency_mhz=frequency_mhz))


@app.command()
def finalize(
    run_id: str,
    config: ConfigOption = Path("config/default.yaml"),
    wait: Annotated[
        bool, typer.Option(help="Wait for workers before stitching and rendering")
    ] = False,
    poll_seconds: Annotated[
        float, typer.Option(help="Queue polling interval while waiting")
    ] = 30.0,
) -> None:
    """Stitch a run and produce visualization artifacts, optionally after its queue drains."""
    _print(finalize_run(_settings(config), run_id, wait=wait, poll_seconds=poll_seconds))


@app.command()
def query(
    latitude: Annotated[float, typer.Option("--latitude", "--lat", help="WGS84 latitude")],
    longitude: Annotated[float, typer.Option("--longitude", "--lon", help="WGS84 longitude")],
    config: ConfigOption = Path("config/default.yaml"),
    height_agl_m: Annotated[float | None, typer.Option()] = None,
    run_id: Annotated[str | None, typer.Option()] = None,
    operator: Annotated[str | None, typer.Option()] = None,
    frequency_mhz: Annotated[float | None, typer.Option()] = None,
    calibrated: Annotated[
        bool, typer.Option(help="Apply data/calibration/latest.json when present")
    ] = True,
) -> None:
    _print(
        PredictionStore(_settings(config))
        .query(
            latitude,
            longitude,
            height_agl_m,
            run_id=run_id,
            operator=operator,
            frequency_mhz=frequency_mhz,
            calibrated=calibrated,
        )
        .model_dump(mode="json")
    )


@app.command("import-measurements")
def import_measurements_command(
    csv_path: Path,
    config: ConfigOption = Path("config/default.yaml"),
) -> None:
    _print(import_rows(_settings(config), csv_path))


@app.command()
def calibrate(
    measurement_path: Path,
    config: ConfigOption = Path("config/default.yaml"),
    run_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    settings = _settings(config)
    baseline = build_baseline(settings, measurement_path, run_id=run_id)
    _print(fit_calibration(settings, baseline))


@app.command()
def validate(config: ConfigOption = Path("config/default.yaml")) -> None:
    report = validate_project(_settings(config))
    _print(report)
    if not report["passed"]:
        raise typer.Exit(code=1)


@app.command()
def serve(
    config: ConfigOption = Path("config/default.yaml"),
    host: Annotated[str, typer.Option()] = "0.0.0.0",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    os.environ["OTTAWA_RT_CONFIG"] = str(config.resolve())
    import uvicorn

    uvicorn.run("ottawa_rt.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
