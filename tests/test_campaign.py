import json
from pathlib import Path

import pytest
import yaml

from ottawa_rt.calibrated_coverage import calibration_for_run
from ottawa_rt.campaign import prepare_campaign, run_campaign
from ottawa_rt.config import load_settings


@pytest.fixture
def campaign_project(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    small = yaml.safe_load(Path("config/demo-6km-2p5m.yaml").read_text(encoding="utf-8"))
    large = yaml.safe_load(Path("config/legget-10km-1m.yaml").read_text(encoding="utf-8"))
    for name, payload in (("small.yaml", small), ("large.yaml", large)):
        (config_dir / name).write_text(yaml.safe_dump(payload))
    (config_dir / "network-ottawa.yaml").write_text(Path("config/network-ottawa.yaml").read_text())
    settings = load_settings(config_dir / "small.yaml")
    settings.paths.ensure()
    run = settings.paths.runs / "source"
    run.mkdir()
    (run / "run.json").write_text(
        json.dumps(
            {
                "model_version": small["project"]["model_version"],
                "scene_xml": "data/scenes/source/scene.xml",
                "frequency_groups_mhz": [2120, 3505],
            }
        )
    )
    model = {
        "model_id": "mobile-fit",
        "run_id": "source",
        "model_version": small["project"]["model_version"],
        "metrics": {s: {"count": 3} for s in ("train", "validation", "test")},
    }
    (settings.paths.calibration / "mobile.json").write_text(json.dumps(model))
    (settings.paths.measurements / "node.csv").write_text("raw,node\n1,2\n")
    cfg = {
        "name": "test-campaign",
        "existing_config": "config/small.yaml",
        "existing_run_id": "source",
        "existing_calibration": "data/calibration/mobile.json",
        "large_config": "config/large.yaml",
        "large_run_id": "large",
        "measurement_input_dir": "data/measurements",
        "large_rf_frequencies_mhz": [2120, 3505],
        "ue_counts": [12, 24],
        "seeds": [41, 42],
    }
    path = config_dir / "campaign.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path, settings


def test_campaign_pins_calibration_and_orders_dependencies(campaign_project):
    path, settings = campaign_project
    directory = prepare_campaign(path)
    plan = json.loads((directory / "plan.json").read_text())
    tasks = plan["tasks"]
    kinds = [t["kind"] for t in tasks]
    calibrate = kinds.index("calibrate")
    assert all(t.get("phase") == "existing" for t in tasks[: kinds.index("data")])
    assert all(t.get("phase") == "large" for t in tasks[calibrate + 1 : -1])
    assert kinds[kinds.index("data") : calibrate + 1] == [
        "data",
        "scene",
        "queue",
        "rf",
        "stitch",
        "calibrate",
    ]
    scenario = yaml.safe_load((settings.paths.root / tasks[0]["scenario"]).read_text())
    assert scenario["calibrated"]
    assert scenario["calibration_model_path"].endswith("existing-calibration.json")
    assert sum(p["count"] for p in scenario["profiles"]) == 12
    assert prepare_campaign(path) == directory
    (directory / "existing-calibration.json").write_text("{}")
    with pytest.raises(ValueError, match="input changed"):
        prepare_campaign(path)


def test_campaign_failure_preserves_dependencies_and_resume(campaign_project, monkeypatch):
    path, _ = campaign_project
    seen = []

    def fake_execute(root, cfg, task, directory, status):
        seen.append(task["id"])
        if len(seen) == 2:
            raise RuntimeError("test failure")

    monkeypatch.setattr("ottawa_rt.campaign.execute_task", fake_execute)
    with pytest.raises(RuntimeError, match="test failure"):
        run_campaign(path)
    directory = prepare_campaign(path)
    status = json.loads((directory / "status.json").read_text())
    assert status["status"] == "failed"
    assert len(status["completed"]) == 1
    assert not (directory / "supervisor.lock").exists()
    monkeypatch.setattr("ottawa_rt.campaign.execute_task", lambda *args: None)
    resumed = run_campaign(path, max_tasks=1)
    assert len(resumed["completed"]) == 2
    assert resumed["status"] == "queued"


def test_explicit_calibration_does_not_follow_latest(campaign_project):
    _, settings = campaign_project
    pinned = settings.paths.calibration / "mobile.json"
    (settings.paths.calibration / "latest.json").write_text(json.dumps({"model_version": "wrong"}))
    assert calibration_for_run(settings, "source") is None
    assert calibration_for_run(settings, "source", model_path=pinned)["model_id"] == "mobile-fit"


def test_campaign_rejects_reusing_data_root(campaign_project):
    path, _ = campaign_project
    large = path.parent / "large.yaml"
    config = yaml.safe_load(large.read_text())
    config["paths"]["data_root"] = "data"
    large.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="isolated"):
        prepare_campaign(path)
