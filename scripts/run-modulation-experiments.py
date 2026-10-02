"""Run a paired, sequential NR MCS sweep; retain each scenario and its results."""

import argparse
import csv
import json
import os
from pathlib import Path

import numpy as np
import yaml

from ottawa_rt.config import load_settings
from ottawa_rt.network import load_scenario, run_network
from ottawa_rt.network_models import NetworkScenario

# Fixed cases vary modulation AND coding rate; they are not isolated modulation tests.
CASES = [
    ("adaptive", {"link_adaptation": "adaptive", "fixed_mcs": None, "mcs_table_index": 1}),
    ("qpsk", {"link_adaptation": "fixed", "fixed_mcs": 4, "mcs_table_index": 1}),
    ("16qam", {"link_adaptation": "fixed", "fixed_mcs": 12, "mcs_table_index": 1}),
    ("64qam", {"link_adaptation": "fixed", "fixed_mcs": 20, "mcs_table_index": 1}),
    ("256qam", {"link_adaptation": "fixed", "fixed_mcs": 20, "mcs_table_index": 2}),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/demo-4km-5m.yaml")
    parser.add_argument("--scenario", type=Path, default=Path("config/network-modulation.yaml"))
    parser.add_argument(
        "--directions", nargs="+", choices=["downlink", "uplink"], default=["downlink", "uplink"]
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[41, 42])
    parser.add_argument("--duration", type=float, help="Override simulated seconds (maximum 60)")
    parser.add_argument("--prefix", help="Unique output prefix; defaults to scenario name")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    settings = load_settings(args.config)
    base = load_scenario(args.scenario).model_dump()
    if base["channel_source"] != "paths":
        raise ValueError("Modulation sweeps require channel_source: paths for link-power pairing")
    prefix = args.prefix or base["name"]
    scenarios = []
    for direction in dict.fromkeys(args.directions):
        for seed in dict.fromkeys(args.seeds):
            for label, case in CASES:
                scenario = NetworkScenario.model_validate(
                    {
                        **base,
                        **case,
                        "direction": direction,
                        "seed": seed,
                        "name": f"{prefix}-{direction}-{label}-s{seed}",
                        **({"duration_s": args.duration} if args.duration is not None else {}),
                    }
                )
                scenarios.append((label, scenario))
    # Validate every case and output path before any simulation.
    directory = settings.paths.data / "experiments" / prefix
    for _, scenario in scenarios:
        if (settings.paths.data / "network" / scenario.name).exists() and not args.overwrite:
            raise FileExistsError(
                f"{scenario.name} exists; choose --prefix or explicitly --overwrite"
            )
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "modulation.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"pid": os.getpid(), "purpose": "sequential modulation sweep"}, handle)
        scenario_dir = directory / "scenarios"
        scenario_dir.mkdir(exist_ok=True)
        rows = []
        positions = {}
        link_powers = {}
        for label, scenario in scenarios:
            path = scenario_dir / f"{scenario.name}.yaml"
            path.write_text(
                yaml.safe_dump(scenario.model_dump(), sort_keys=False), encoding="utf-8"
            )
            if args.prepare_only:
                continue
            result = run_network(settings, scenario, overwrite=args.overwrite)
            actual = [
                (u["ue_id"], u["x_m"], u["y_m"], u["serving_sector_id"]) for u in result["ues"]
            ]
            if scenario.seed in positions and positions[scenario.seed] != actual:
                raise AssertionError("Paired cases changed UE placement or serving association")
            positions[scenario.seed] = actual
            # The network abstraction consumes path-energy link powers, not
            # path ordering. GPU path records can be permuted between traces.
            with np.load(settings.paths.data / "network" / scenario.name / "channels.npz") as data:
                powers = {key: data[key] for key in ("rss_dbm", "reciprocal_gain_db")}
            if scenario.seed in link_powers:
                for key, power in powers.items():
                    if not np.allclose(power, link_powers[scenario.seed][key], rtol=0, atol=1e-5):
                        raise AssertionError(
                            "Paired cases changed RT link power by more than 1e-5 dB"
                        )
            link_powers.setdefault(scenario.seed, powers)
            summary = result["summary"]
            rows.append(
                {
                    "name": scenario.name,
                    "direction": scenario.direction,
                    "case": label,
                    "seed": scenario.seed,
                    "table": scenario.mcs_table_index,
                    "fixed_mcs": scenario.fixed_mcs,
                    "duration_s": scenario.duration_s,
                    "throughput_mbps": summary["total_throughput_mbps"],
                    "goodput_mbps": summary["total_goodput_mbps"],
                    "predicted_tbler": summary["mean_predicted_tbler"],
                    "observed_tbler": summary["observed_tbler"],
                    "radio_queue_p95_ms": summary["radio_queue_latency_ms"]["p95"],
                    "tail_drop_ratio": summary["packet_loss_ratio"],
                    "delivery_ratio": summary["packet_delivery_ratio"],
                    "pending_packets": summary["pending_packets"],
                }
            )
            # Checkpoint comparison after each complete run.
            (directory / "comparison.json").write_text(
                json.dumps(rows, indent=2, allow_nan=False), encoding="utf-8"
            )
            with (directory / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(json.dumps(rows[-1]), flush=True)
        print(
            f"{'Prepared' if args.prepare_only else 'Completed'} {len(scenarios)} cases in {directory}"
        )
    finally:
        lock.unlink()


if __name__ == "__main__":
    main()
