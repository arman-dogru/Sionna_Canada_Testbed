"""Repeat real Ottawa GPU channel traces and compare traffic/bandwidth scenarios.

Run from the repository root with .venv/Scripts/python.exe scripts/validate-network.py.
This writes reproducible experiment outputs under data/network/ and a small report
under outputs/. Synthetic unit tests remain separate from these scene checks.
"""

import json
from pathlib import Path

from ottawa_rt.config import load_settings
from ottawa_rt.network import load_scenario, run_network
from ottawa_rt.network_models import NetworkScenario


def main():
    settings = load_settings("config/demo-4km-5m.yaml")
    original = load_scenario(Path("config/network-ottawa.yaml")).model_dump()
    baseline_path = settings.paths.data / "network" / original["name"] / "result.json"
    baseline = (
        json.loads(baseline_path.read_text())
        if baseline_path.exists()
        else run_network(settings, NetworkScenario.model_validate(original))
    )
    variants = [
        ("ottawa-mixed-repeat", {}),
        ("ottawa-mixed-40mhz", {"bandwidth_mhz": 40}),
        (
            "ottawa-light-traffic",
            {
                "profiles": [
                    dict(p, offered_mbps=p["offered_mbps"] / 20) for p in original["profiles"]
                ]
            },
        ),
        (
            "ottawa-overload",
            {
                "profiles": [
                    dict(p, offered_mbps=p["offered_mbps"] * 10) for p in original["profiles"]
                ]
            },
        ),
    ]
    results = [baseline]
    for name, updates in variants:
        selected = NetworkScenario.model_validate({**original, **updates, "name": name})
        result = run_network(settings, selected, overwrite=True)
        results.append(result)
        print(name, json.dumps(result["summary"]), flush=True)
    repeat, wide, light, overload = results[1:]
    assert len(repeat["ues"]) == len(baseline["ues"]) and all(
        all(after[key] == before[key] for key in before)
        for before, after in zip(baseline["ues"], repeat["ues"], strict=True)
    ), "Identical seed/channel inputs must reproduce UE results"
    assert repeat["time_series"] == baseline["time_series"]
    assert wide["summary"]["total_goodput_mbps"] > baseline["summary"]["total_goodput_mbps"]
    assert (
        light["summary"]["radio_queue_latency_ms"]["p95"]
        < baseline["summary"]["radio_queue_latency_ms"]["p95"]
    )
    assert overload["summary"]["packet_loss_ratio"] > baseline["summary"]["packet_loss_ratio"]
    secondary_id = "demo-6km-2p5m-multiband-20260918-r2"
    secondary = NetworkScenario.model_validate(
        {
            **original,
            "name": "ottawa-6km-smoke",
            "run_id": secondary_id,
            "duration_s": 0.2,
            "profiles": [dict(p, count=2) for p in original["profiles"]],
        }
    )
    result = run_network(load_settings("config/demo-6km-2p5m.yaml"), secondary, overwrite=True)
    assert result["summary"]["total_goodput_mbps"] > 0
    results.append(result)
    report = {
        "checks": {
            "repeatable": True,
            "bandwidth_changes_capacity": True,
            "lower_load_reduces_delay": True,
            "overload_increases_loss": True,
            "second_ottawa_scene": True,
        },
        "experiments": [
            {"name": r["name"], "channel_trace": r["channel_trace"], "summary": r["summary"]}
            for r in results
        ],
    }
    output = settings.paths.root / "outputs/network-verification-20261001.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("PASS", output, flush=True)


if __name__ == "__main__":
    main()
