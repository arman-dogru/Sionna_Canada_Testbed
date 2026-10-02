"""Dependency-ordered, resumable calibrated RF and UE experiment campaigns."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ottawa_rt.calibrated_coverage import calibration_for_run
from ottawa_rt.config import load_settings
from ottawa_rt.data.provenance import sha256_file
from ottawa_rt.network_models import SAFE_ID, NetworkScenario


class CampaignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(pattern=SAFE_ID)
    existing_config: str
    existing_run_id: str = Field(pattern=SAFE_ID)
    existing_calibration: str
    large_config: str
    large_run_id: str = Field(pattern=SAFE_ID)
    measurement_input_dir: str
    large_rf_frequencies_mhz: list[float] = Field(min_length=1)
    ue_counts: list[int] = Field(min_length=1)
    seeds: list[int] = Field(min_length=1)
    duration_s: float = Field(default=5, gt=0, le=60)
    primary_frequency_mhz: float = 3505
    reference_frequency_mhz: float = 2120

    @model_validator(mode="after")
    def check_counts(self):
        if any(n < 3 or n > 498 or n % 3 for n in self.ue_counts):
            raise ValueError("UE counts must be multiples of three between 3 and 498")
        if len(set(self.ue_counts)) != len(self.ue_counts) or len(set(self.seeds)) != len(
            self.seeds
        ):
            raise ValueError("Counts and seeds must be unique")
        if any(s < 0 or s > 2**32 - 1 for s in self.seeds):
            raise ValueError("Seed outside supported range")
        if self.primary_frequency_mhz not in self.large_rf_frequencies_mhz:
            raise ValueError("Large RF run must include primary UE frequency")
        if self.reference_frequency_mhz not in self.large_rf_frequencies_mhz:
            raise ValueError("Large RF run must include reference UE frequency")
        return self


CONDITIONS = {
    "mixed": {},
    "light": {"load_factor": 0.05},
    "overload": {"load_factor": 10.0},
    "wideband": {"bandwidth_mhz": 40},
    "noisy": {"noise_figure_db": 10},
    "less-dl": {"downlink_fraction": 0.5},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def scenario_values(
    cfg: CampaignConfig,
    phase: str,
    count: int,
    seed: int,
    condition: str,
    model_path: str,
    *,
    root: Path,
) -> dict:
    settings = load_settings(
        root / (cfg.existing_config if phase == "existing" else cfg.large_config)
    )
    base = yaml.safe_load((settings.paths.root / "config/network-ottawa.yaml").read_text())
    overrides = dict(CONDITIONS.get(condition, {}))
    load_factor = overrides.pop("load_factor", 1)
    base.update(
        name=f"{cfg.name}-{phase}-{condition}-u{count}-s{seed}",
        run_id=cfg.existing_run_id if phase == "existing" else cfg.large_run_id,
        frequency_mhz=cfg.reference_frequency_mhz
        if condition == "reference"
        else cfg.primary_frequency_mhz,
        calibrated=True,
        calibration_model_path=model_path,
        duration_s=cfg.duration_s,
        seed=seed,
        placement_width_m=10000 if condition == "area-wide" else 1000,
        **overrides,
    )
    base["profiles"] = [
        dict(p, count=count // 3, offered_mbps=p["offered_mbps"] * load_factor)
        for p in base["profiles"]
    ]
    return NetworkScenario.model_validate(base).model_dump()


def prepare_campaign(config_path: Path) -> Path:
    config_path = config_path.resolve()
    root = config_path.parent.parent
    cfg = CampaignConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    existing = load_settings(root / cfg.existing_config)
    large = load_settings(root / cfg.large_config)
    for value in (existing.paths.data, large.paths.data):
        value.relative_to(root)
    if existing.paths.data == large.paths.data:
        raise ValueError("Large campaign must have an isolated data_root")
    manifest = json.loads((existing.paths.runs / cfg.existing_run_id / "run.json").read_text())
    model_path = root / cfg.existing_calibration
    model = calibration_for_run(existing, cfg.existing_run_id, model_path=model_path)
    if model is None:
        raise ValueError("Existing mobile-node calibration is incompatible with the source run")
    if any(float(model["metrics"][s]["count"]) == 0 for s in ("train", "validation", "test")):
        raise ValueError("Existing fit requires training, validation and test evidence")
    if any(
        f not in manifest["frequency_groups_mhz"]
        for f in (cfg.primary_frequency_mhz, cfg.reference_frequency_mhz)
    ):
        raise ValueError("Existing RF run lacks a selected UE frequency")
    if large.cell_size_m != 1 or float(large.raw["area"]["max_width_m"]) != 10000:
        raise ValueError("Large stage requires 10 km width and 1 m receiver cells")
    if existing.anchor != large.anchor:
        raise ValueError("Both stages must use the same anchor for comparisons")
    state_dir = root / "data/experiments" / cfg.name
    plan_path = state_dir / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        if plan["campaign_config_sha256"] != sha256_file(config_path):
            raise ValueError("Campaign configuration changed; use a new campaign name")
        for item in plan["inputs"]:
            if sha256_file(root / item["path"]) != item["sha256"]:
                raise ValueError(f"Campaign input changed: {item['path']}; use a new name")
        return state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    pin_path = state_dir / "existing-calibration.json"
    shutil.copy2(model_path, pin_path)
    pinned = pin_path.relative_to(root).as_posix()
    large_model = (large.paths.calibration / "campaign-model.json").relative_to(root).as_posix()
    tasks = []
    for phase, model_file in (("existing", pinned), ("large", large_model)):
        for count in cfg.ue_counts:
            for seed in cfg.seeds:
                for condition in (*CONDITIONS, "reference"):
                    values = scenario_values(
                        cfg, phase, count, seed, condition, model_file, root=root
                    )
                    path = state_dir / "scenarios" / f"{values['name']}.yaml"
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
                    tasks.append(
                        {
                            "id": values["name"],
                            "kind": "network",
                            "phase": phase,
                            "scenario": path.relative_to(root).as_posix(),
                            "config": cfg.existing_config
                            if phase == "existing"
                            else cfg.large_config,
                        }
                    )
        if phase == "large":
            for count in (cfg.ue_counts[0], cfg.ue_counts[-1]):
                for seed in cfg.seeds:
                    values = scenario_values(
                        cfg, phase, count, seed, "area-wide", model_file, root=root
                    )
                    path = state_dir / "scenarios" / f"{values['name']}.yaml"
                    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
                    tasks.append(
                        {
                            "id": values["name"],
                            "kind": "network",
                            "phase": phase,
                            "scenario": path.relative_to(root).as_posix(),
                            "config": cfg.large_config,
                        }
                    )
    preparations = [
        {"id": f"large-{stage}", "kind": stage}
        for stage in ("data", "scene", "queue", "rf", "stitch", "calibrate")
    ]
    ordered = (
        [t for t in tasks if t["phase"] == "existing"]
        + preparations
        + [t for t in tasks if t["phase"] == "large"]
        + [{"id": "comparison-report", "kind": "report"}]
    )
    inputs = [
        config_path,
        existing.config_path,
        large.config_path,
        root / "config/network-ottawa.yaml",
        model_path,
    ]
    inputs.extend(sorted((root / cfg.measurement_input_dir).glob("*.csv")))
    if len(inputs) == 5:
        raise FileNotFoundError("Mobile-node input CSVs are required")
    inputs.append(pin_path)
    plan = {
        "schema_version": 1,
        "created_at": utc_now(),
        "campaign": cfg.model_dump(),
        "campaign_config_sha256": sha256_file(config_path),
        "inputs": [
            {"path": p.relative_to(root).as_posix(), "sha256": sha256_file(p)} for p in inputs
        ],
        "tasks": ordered,
    }
    for task in ordered:
        if task.get("scenario"):
            task["scenario_sha256"] = sha256_file(root / task["scenario"])
    write_json(plan_path, plan)
    write_json(
        state_dir / "status.json",
        {
            "name": cfg.name,
            "status": "queued",
            "completed": [],
            "total_tasks": len(ordered),
            "network_scenarios": len(tasks),
            "updated_at": utc_now(),
        },
    )
    return state_dir


def run_command(root: Path, arguments: list[str], log_path: Path, environment=None) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.run(
            [sys.executable, "-m", "ottawa_rt.cli", *arguments],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )


def execute_rf(root: Path, cfg: CampaignConfig, state_dir: Path, status: dict) -> None:
    from ottawa_rt.jobs import FileJobQueue

    settings = load_settings(root / cfg.large_config)
    queue = FileJobQueue(settings.paths.runs / cfg.large_run_id / "queue")
    processes: dict[int, tuple[subprocess.Popen, object]] = {}
    try:
        while True:
            counts = queue.counts()
            status.update(rf_queue=counts, updated_at=utc_now())
            write_json(state_dir / "status.json", status)
            for gpu, (process, log) in list(processes.items()):
                if process.poll() is not None:
                    log.close()
                    del processes[gpu]
                    if process.returncode:
                        raise RuntimeError(f"GPU {gpu} worker exited with {process.returncode}")
            if counts["failed"]:
                raise RuntimeError(f"RF jobs failed: {counts}; inspect GPU logs and failed records")
            if not counts["pending"] and not processes:
                if counts["processing"]:
                    raise RuntimeError(
                        "Orphan RF claims remain; verify worker PIDs before recovery"
                    )
                break
            for gpu in range(int(settings.raw["compute"]["local_gpu_count"])):
                if gpu not in processes and counts["pending"]:
                    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu))
                    log = (state_dir / "logs" / f"gpu-{gpu}.log").open("a", encoding="utf-8")
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "ottawa_rt.cli",
                            "worker",
                            cfg.large_run_id,
                            "--config",
                            cfg.large_config,
                            "--max-jobs",
                            "4",
                        ],
                        cwd=root,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    processes[gpu] = (process, log)
            time.sleep(10)
    finally:
        for process, log in processes.values():
            if process.poll() is None:
                process.terminate()
            process.wait()
            log.close()


def execute_task(root: Path, cfg: CampaignConfig, task: dict, state_dir: Path, status: dict):
    settings = load_settings(root / cfg.large_config)
    log = state_dir / "logs" / f"{task['id']}.log"
    kind = task["kind"]
    if kind == "network":
        scenario_path = root / task["scenario"]
        if sha256_file(scenario_path) != task["scenario_sha256"]:
            raise ValueError("Generated scenario changed; prepare a new campaign name")
        scenario = NetworkScenario.model_validate(yaml.safe_load(scenario_path.read_text()))
        selected = load_settings(root / task["config"])
        output = selected.paths.data / "network" / scenario.name / "result.json"
        model = calibration_for_run(
            selected, scenario.run_id, model_path=root / scenario.calibration_model_path
        )
        if model is None:
            raise ValueError("Pinned calibration unavailable or incompatible")
        if output.exists():
            saved = json.loads(output.read_text())
            digest = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
            if (
                saved["scenario"] != scenario.model_dump()
                or saved["source_sha256"].get("calibration_model_sha256") != digest
            ):
                raise ValueError(f"Existing result has different inputs: {output}")
        else:
            run_command(
                root,
                ["simulate-network", "--config", task["config"], "--scenario", task["scenario"]],
                log,
            )
    elif kind == "data":
        settings.paths.ensure()
        existing = load_settings(root / cfg.existing_config)
        for subdir, pattern in (("ised", "*.zip"), ("ottawa_lod1", "*.gdb.zip")):
            destination = settings.paths.raw / subdir
            destination.mkdir(parents=True, exist_ok=True)
            for source in (existing.paths.raw / subdir).glob(pattern):
                target = destination / source.name
                if not target.exists():
                    shutil.copy2(source, target)
        run_command(root, ["fetch-data", "--config", cfg.large_config, "--width-m", "10000"], log)
        run_command(
            root, ["normalize-ised", "--config", cfg.large_config, "--width-m", "10000"], log
        )
    elif kind == "scene":
        run_command(root, ["build-scene", "--config", cfg.large_config, "--width-m", "10000"], log)
    elif kind == "queue":
        args = [
            "simulate",
            "--config",
            cfg.large_config,
            "--width-m",
            "10000",
            "--run-id",
            cfg.large_run_id,
        ]
        for f in cfg.large_rf_frequencies_mhz:
            args.extend(["--frequency-mhz", str(f)])
        run_command(root, args, log)
    elif kind == "rf":
        execute_rf(root, cfg, state_dir, status)
    elif kind == "stitch":
        run_command(root, ["stitch", cfg.large_run_id, "--config", cfg.large_config], log)
        report = json.loads(
            (settings.paths.runs / cfg.large_run_id / "stitched/stitch-report.json").read_text()
        )
        complete = [g for g in report["frequency_groups"] if g["status"] == "complete"]
        if len(complete) != len(cfg.large_rf_frequencies_mhz):
            raise RuntimeError("Incomplete RF stitch; calibration and UE stages cannot start")
    elif kind == "calibrate":
        from ottawa_rt.calibrated_coverage import ensure_calibrated_coverage
        from ottawa_rt.calibration import build_baseline, calibrate, import_measurements
        from ottawa_rt.measurement_snapshot import build_measurement_snapshot

        snapshot = settings.paths.measurements / "snapshots" / cfg.name
        if not (snapshot / "manifest.json").exists():
            if snapshot.exists() and any(snapshot.iterdir()):
                raise RuntimeError(
                    "Partial immutable snapshot; inspect it before choosing a new name"
                )
            build_measurement_snapshot(
                settings,
                root / cfg.measurement_input_dir,
                run_id=cfg.large_run_id,
                snapshot_name=cfg.name,
            )
        canonical = snapshot / "receiver-measurements.csv"
        imported = import_measurements(settings, canonical)
        baseline = build_baseline(settings, Path(imported["output"]), run_id=cfg.large_run_id)
        model = calibrate(settings, baseline)
        if any(model["metrics"][split]["count"] == 0 for split in ("train", "validation", "test")):
            raise RuntimeError("Fresh fit lacks a holdout split; inspect mobile-node support")
        model["mobile_node_snapshot"] = {
            "manifest": settings.portable_path(snapshot / "manifest.json"),
            "manifest_sha256": sha256_file(snapshot / "manifest.json"),
            "canonical_sha256": sha256_file(canonical),
        }
        write_json(settings.paths.calibration / "campaign-model.json", model)
        write_json(settings.paths.calibration / "latest.json", model)
        ensure_calibrated_coverage(settings, cfg.large_run_id, model)
        write_json(state_dir / "large-calibration-report.json", model)
    elif kind == "report":
        write_comparison(root, cfg, state_dir)
    else:
        raise ValueError(f"Unknown campaign task: {kind}")


def write_comparison(root: Path, cfg: CampaignConfig, state_dir: Path) -> None:
    rows = []
    for phase, config in (("existing", cfg.existing_config), ("large", cfg.large_config)):
        settings = load_settings(root / config)
        for path in sorted(
            (settings.paths.data / "network").glob(f"{cfg.name}-{phase}-*/result.json")
        ):
            result = json.loads(path.read_text())
            summary, scenario = result["summary"], result["scenario"]
            rows.append(
                {
                    "name": result["name"],
                    "phase": phase,
                    "ue_count": summary["ue_count"],
                    "condition": result["name"].removeprefix(f"{cfg.name}-{phase}-").split("-u")[0],
                    "seed": scenario["seed"],
                    "frequency_mhz": scenario["frequency_mhz"],
                    "placement_width_m": scenario["placement_width_m"],
                    "bandwidth_mhz": scenario["bandwidth_mhz"],
                    "goodput_mbps": summary["total_goodput_mbps"],
                    "delivery_ratio": summary["packet_delivery_ratio"],
                    "drop_ratio": summary["packet_loss_ratio"],
                    "pending_packets": summary["pending_packets"],
                    "radio_p95_ms": summary["radio_queue_latency_ms"]["p95"],
                    "fairness": summary["jain_fairness"],
                    "calibration_model_id": result["calibration"]["model_id"],
                    "calibration_support": result["calibration"]["support"],
                    "result_path": path.relative_to(root).as_posix(),
                }
            )
    write_json(state_dir / "comparison.json", {"generated_at": utc_now(), "experiments": rows})
    if rows:
        with (state_dir / "comparison.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def run_campaign(config_path: Path, *, max_tasks: int | None = None) -> dict:
    state_dir = prepare_campaign(config_path)
    root = config_path.resolve().parent.parent
    plan = json.loads((state_dir / "plan.json").read_text())
    cfg = CampaignConfig.model_validate(plan["campaign"])
    lock = state_dir / "supervisor.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Campaign supervisor already claimed; inspect {lock}") from exc
    os.write(descriptor, f"{os.getpid()}\n".encode())
    os.close(descriptor)
    status = json.loads((state_dir / "status.json").read_text())
    status.update(status="running", supervisor_pid=os.getpid(), updated_at=utc_now(), error=None)
    (state_dir / "logs").mkdir(exist_ok=True)
    processed = 0
    try:
        for task in plan["tasks"]:
            if task["id"] in status["completed"]:
                continue
            status.update(current_task=task["id"], updated_at=utc_now())
            write_json(state_dir / "status.json", status)
            print(f"{utc_now()} START {task['id']}", flush=True)
            execute_task(root, cfg, task, state_dir, status)
            status["completed"].append(task["id"])
            status.update(updated_at=utc_now())
            write_json(state_dir / "status.json", status)
            print(f"{utc_now()} DONE {task['id']}", flush=True)
            # Keep an up-to-date comparison even before the full campaign finishes.
            if task["kind"] == "network":
                write_comparison(root, cfg, state_dir)
            processed += 1
            if max_tasks is not None and processed >= max_tasks:
                break
        status.update(
            status="complete" if len(status["completed"]) == len(plan["tasks"]) else "queued",
            current_task=None,
            updated_at=utc_now(),
        )
    except BaseException as exc:
        status.update(status="failed", error=f"{type(exc).__name__}: {exc}", updated_at=utc_now())
        raise
    finally:
        write_json(state_dir / "status.json", status)
        lock.unlink(missing_ok=True)
    return status
