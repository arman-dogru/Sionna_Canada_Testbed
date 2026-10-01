import hashlib
import json
import time
from pathlib import Path
from threading import Event

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ottawa_rt.api import create_app
from ottawa_rt.calibrated_coverage import calibrated_layers, ensure_calibrated_coverage
from ottawa_rt.config import load_settings
from ottawa_rt.geo import thermal_noise_dbm
from ottawa_rt.query import PredictionStore
from ottawa_rt.stitch import stitch_run


@pytest.fixture
def project(tmp_path):
    config = tmp_path / "config" / "default.yaml"
    config.parent.mkdir()
    config.write_text(Path("config/default.yaml").read_text())
    settings = load_settings(config)
    settings.paths.ensure()
    (settings.paths.processed / "sectors.jsonl").write_text("")
    run_id = "measured"
    run_dir = settings.paths.runs / run_id
    (run_dir / "tiles").mkdir(parents=True)
    scene_dir = settings.paths.scenes / "test"
    scene_dir.mkdir()
    (scene_dir / "scene_metadata.json").write_text("{}")
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tile_count": 1,
                "width_m": 20,
                "cell_size_m": 10,
                "model_version": "baseline-v1",
                "scene_xml": "data/scenes/test/scene.xml",
                "frequency_groups_mhz": [900, 1900],
            }
        )
    )
    for frequency, strength in ((900, -90.0), (1900, -80.0)):
        rsrp = np.asarray(
            [np.full((2, 2), strength), np.full((2, 2), strength - 2)], dtype=np.float32
        )
        rsrp[:, 0, 1] = -np.inf
        np.savez_compressed(
            run_dir / "tiles" / f"r000-c000-{frequency}p0MHz.npz",
            rsrp_dbm=rsrp,
            rss_dbm=rsrp + 20,
            path_gain_db=rsrp - 40,
            sinr_db=np.where(np.isfinite(rsrp), 1.0, -np.inf),
            sector_ids=np.asarray(["a", "b"]),
            frequencies_mhz=np.asarray([frequency, frequency]),
            bandwidths_mhz=np.asarray([20.0, 20.0]),
            operators=np.asarray(["Carrier", "Carrier"]),
            technologies=np.asarray(["LTE", "LTE"]),
            defaults_applied=np.asarray(["[]", "[]"]),
            tile_center=np.asarray([0.0, 0.0]),
            tile_size_m=np.asarray(20),
            overlap_m=np.asarray(0),
            cell_size_m=np.asarray(10),
        )
    stitch_run(settings, run_id)
    model = {
        "model_id": "fit-1",
        "run_id": run_id,
        "model_version": "baseline-v1",
        "global_receiver_offset_db": 0,
        "frequency_group_offsets_db": {"sub-1GHz": 12},
        "sector_eirp_offsets_db": {"a": -6, "b": 6},
    }
    (settings.paths.calibration / "latest.json").write_text(json.dumps(model))
    return settings, run_id, model


def test_calibration_reselects_server_and_recomputes_sinr(project):
    settings, run_id, model = project
    path = settings.paths.runs / run_id / "tiles" / "r000-c000-1900p0MHz.npz"
    before = hashlib.sha256(path.read_bytes()).digest()
    with np.load(path) as data:
        layers = calibrated_layers(data, model, 7, {})
    assert layers["rsrp_dbm"][:, 1, 1].tolist() == [-86, -76]
    assert np.isneginf(layers["rsrp_dbm"][:, 0, 1]).all()
    signal = 10 ** (-56 / 10)
    interference = 10 ** (-66 / 10)
    noise = 10 ** (thermal_noise_dbm(20, 7) / 10)
    assert layers["sinr_db"][1, 1, 1] == pytest.approx(
        10 * np.log10(signal / (interference + noise))
    )
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_calibrated_stitch_reselects_combined_band_and_keeps_original(project):
    settings, run_id, model = project
    raw = settings.paths.runs / run_id / "stitched" / "all-bands.npz"
    before = hashlib.sha256(raw.read_bytes()).digest()
    output = ensure_calibrated_coverage(settings, run_id, model)
    with np.load(output / "all-bands.npz") as data:
        assert data["max_rsrp_dbm"][1, 1] == -74
        assert data["frequency_mhz"][1, 1] == 900
        assert str(data["sector_ids"][data["serving_sector_index"][1, 1]]) == "b"
        assert data["serving_sector_index"][0, 1] == 0
        assert data["max_path_gain_db"][1, 1] == -120
    assert hashlib.sha256(raw.read_bytes()).digest() == before


def test_api_changes_values_and_images_and_invalidates_same_id_refit(project):
    settings, run_id, model = project
    url = f"/v1/coverage/{run_id}/1900.0MHz.npz?source=stitched&stride=1"
    with TestClient(create_app(settings)) as client:
        raw = client.get(url + "&calibrated=false").json()
        calibrated = client.get(url).json()
        assert raw["values"][1][1] == -80
        assert calibrated["values"][1][1] == -76
        assert calibrated["calibrated"] is True
        assert calibrated["values"][0][1] == -200
        assert calibrated["image_url"] != raw["image_url"]
        raw_png = client.get(raw["image_url"]).content
        calibrated_png = client.get(calibrated["image_url"]).content
        assert raw_png != calibrated_png
        model["sector_eirp_offsets_db"]["b"] = 10
        (settings.paths.calibration / "latest.json").write_text(json.dumps(model))
        refitted = client.get(url).json()
        assert refitted["values"][1][1] == -72
        assert refitted["artifact_version"] != calibrated["artifact_version"]
        assert refitted["image_url"] != calibrated["image_url"]
        path_gain = client.get(url + "&metric=path_gain").json()
        assert path_gain["calibrated"] is False
        assert path_gain["values"][1][1] == -120


def test_calibrated_query_ranks_all_candidates_before_limit(project, monkeypatch):
    settings, run_id, _ = project
    monkeypatch.setattr("ottawa_rt.query._local_xy", lambda *_: (0.0, 0.0))
    store = PredictionStore(settings)
    raw = store.query(
        *settings.anchor, run_id=run_id, frequency_mhz=1900, limit=1, calibrated=False
    )
    corrected = store.query(*settings.anchor, run_id=run_id, frequency_mhz=1900, limit=1)
    assert raw.serving_sector_id == "a"
    assert corrected.serving_sector_id == "b"
    assert corrected.predictions[0].rsrp_dbm == -76
    with TestClient(create_app(settings)) as client:
        raster = client.get(
            f"/v1/coverage/{run_id}/1900.0MHz.npz?source=stitched&stride=1&metric=sinr"
        ).json()
    assert corrected.predictions[0].sinr_db == pytest.approx(raster["values"][1][1])


def test_calibration_does_not_cross_scientific_model_versions(project):
    settings, run_id, model = project
    model["model_version"] = "another-physical-model"
    (settings.paths.calibration / "latest.json").write_text(json.dumps(model))
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/runs").json()[0]["calibration_available"] is False
        raster = client.get(f"/v1/coverage/{run_id}/1900.0MHz.npz?source=stitched&stride=1").json()
        assert raster["calibrated"] is False
        assert raster["values"][1][1] == -80


def test_metadata_build_returns_progress_and_does_not_block_cached_maps(project, monkeypatch):
    settings, run_id, model = project
    ensure_calibrated_coverage(settings, run_id, model)
    changed_model = {**model, "global_receiver_offset_db": 1}
    model_path = settings.paths.calibration / "latest.json"
    model_path.write_text(json.dumps(changed_model))
    started, release, finished = Event(), Event(), Event()
    original_stitch = stitch_run
    calls = []

    def slow_stitch(*args, **kwargs):
        calls.append(args[1])
        kwargs["progress"](900, 1, 2)
        started.set()
        assert release.wait(5)
        try:
            return original_stitch(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr("ottawa_rt.stitch.stitch_run", slow_stitch)
    url = f"/v1/coverage/{run_id}/1900.0MHz.npz?source=stitched&format=metadata"
    try:
        with TestClient(create_app(settings)) as client:
            first = client.get(url)
            assert first.status_code == 202
            assert first.headers["cache-control"] == "no-store"
            assert started.wait(1)
            pending = client.get(url)
            assert pending.status_code == 202
            assert pending.json()["completed_bands"] == 1
            assert pending.json()["total_bands"] == 2
            assert calls == [run_id]
            assert client.get(url + "&calibrated=false").status_code == 200
            # A completed version must be immediately usable while another version builds.
            model_path.write_text(json.dumps(model))
            before = time.monotonic()
            assert client.get(url).status_code == 200
            assert time.monotonic() - before < 1
            model_path.write_text(json.dumps(changed_model))
            release.set()
            assert finished.wait(2)
            ready = client.get(url)
            assert ready.status_code == 200
            assert ready.json()["calibrated"] is True
    finally:
        release.set()
        finished.wait(2)


def test_metadata_build_error_is_reported_and_can_be_retried(project, monkeypatch):
    settings, run_id, _ = project
    failed = Event()
    original_stitch = stitch_run

    def broken_stitch(*args, **kwargs):
        failed.set()
        raise OSError("Test disk error")

    monkeypatch.setattr("ottawa_rt.stitch.stitch_run", broken_stitch)
    url = f"/v1/coverage/{run_id}/1900.0MHz.npz?source=stitched&format=metadata"
    with TestClient(create_app(settings)) as client:
        assert client.get(url).status_code == 202
        assert failed.wait(1)
        # The failure is published by the worker after the event is set.
        deadline = time.monotonic() + 1
        while True:
            response = client.get(url)
            if response.status_code != 202 or time.monotonic() >= deadline:
                break
        assert response.status_code == 500
        assert response.json()["detail"] == "Test disk error"
        monkeypatch.setattr("ottawa_rt.stitch.stitch_run", original_stitch)
        assert client.get(url).status_code == 202
        deadline = time.monotonic() + 2
        while True:
            response = client.get(url)
            if response.status_code != 202 or time.monotonic() >= deadline:
                break
        assert response.status_code == 200
