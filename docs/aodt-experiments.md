# AODT configurations, UEs, and experiment scheduling

Companion to [the AODT setup runbook](aodt.md), checked on **2026-10-02** against the **1.5.1** interfaces. Examples below are operator recipes for a supported AODT installation; they have not been executed on this Windows workstation. `studies/legget/base-ran.yaml` and converted Ottawa assets are prerequisites to create, not files already supplied by this repository.

Start with [the beginner run guide](../RUN_AODT_GUIDE_FOR_NON_TECHNICALS.md) for operating steps; use [the configuration reference](aodt-configuration.md) for exact fields, units, and change dependencies.

## 1. Understand the configuration files

| File or artifact | Responsibility |
| --- | --- |
| Native simulation YAML | Scene, storage/export, RU/DU/panels, UEs, EM solver, seed, and radio timeline. |
| Builder asset configuration | Default JSON/assets used when constructing a native scenario from scratch. |
| `worker/config_ran.json` | Worker-wide advanced RAN choices: scheduler, TDD pattern, HARQ, noise figure, MCS/CSI, scheduling limits. |
| Worker Compose configuration and release manifest | Services, image versions, GPU/runtime, endpoints, and persistent mounts. |
| Experiment manifest and saved results | Exact effective inputs, calibration evidence, identities, logs, and output catalog references. |

Build complete native YAMLs using `_config` and OmegaConf. The high-level sections are `db`, `gis`, and `sim`; our `config/network-ottawa.yaml` and campaign YAMLs use a different schema. [Native configuration guide](https://docs.nvidia.com/aerial/aodt/configuring-sim-yaml).

Useful native paths in the [complete RAN example](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/tests/assets/quickstart_batched_ran.yaml):

| Path | Purpose |
| --- | --- |
| `db.sim_id` | Unique simulation/database identifier. |
| `db.opt_in_tables`, `db.parquet_export`, `db.s3_config` | Persisted tables and export/storage configuration. |
| `gis.scene.scene_url` | Prepared scene asset prefix. |
| `sim.DUs`, `sim.RUs`, `sim.Panels` | Deployment and antenna definitions. |
| `sim.UEs` | Explicit UE definitions and waypoints. |
| `sim.Scenario.update[].attributes` | Scenario settings listed below. |

Inside a Scenario update, inspect `sim_is_full`, `sim_simulation_mode`, `sim_batches`, `sim_slots_per_batch`, `sim_samples_per_slot`, `sim_is_seeded`, and `sim_seed`. Procedural placement adds `sim_num_procedural_ues` and a spawn zone. Solver settings include `sim_em_rays` (thousands of rays), `sim_em_interactions`, and `sim_em_max_num_paths_per_ant_pair`. Treat this table as a navigation reference, **not** a complete configuration to submit.

Prefer builder methods over copying isolated YAML fragments. `SimConfig.from_yaml_file()` imports a complete client-generated file and exposes editable objects; it does not preserve comments or original YAML formatting. [Builder API](https://docs.nvidia.com/aerial/aodt/api/config).

## 2. Keep the deployment and radio timeline consistent

A DU supplies the radio configuration; an RU represents the transmitting/receiving radio location and panel; a UE is the user terminal. Keep valid IDs and create the referenced DU before its RU. Use the [native builder example](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/examples/example_client_yaml_config.py) for complete construction, including storage and panels.

For an Ottawa deployment, record the sector-to-RU mapping, antenna height, bearing/tilt convention, carrier, and radiated-power interpretation. Changing DU frequency does not update its RUs automatically. Check RU and panel reference frequencies too. Input powers use dBm; output tables can use different units. [Builder API](https://docs.nvidia.com/aerial/aodt/api/config).

RAN uses slots/symbols; EM uses duration/interval settings. At 30 kHz spacing, use **0.5 ms per slot** for experiment sizing:

```text
pooled simulated radio time = batches × slots_per_batch × 0.0005 seconds
100 slots in one batch       = 0.05 seconds: a short smoke run
10,000 slots in one batch    = 5 seconds: a longer radio observation
```

This is simulated time, not wall-clock runtime. `realizations_per_slot=1` or `14` changes channel sampling within the slot, not its duration. [RAN timeline](https://docs.nvidia.com/aerial/aodt/ran-simulations).

**Multiple batches are separate realizations.** Manual/GPX trajectories repeat across batches; procedural placement changes. Ten 1,000-slot batches should be analyzed as ten 0.5-second realizations, rather than assumed to be one continuous 5-second traffic trace. [Mobility model](https://docs.nvidia.com/aerial/aodt/intro-mobility-model).

## 3. Place UEs

### Fixed outdoor UEs

Save this as `studies/legget/make_manual.py` in the AODT client checkout, after preparing `base-ran.yaml`. Its two coordinates are illustrative placements to inspect against the converted scene, not surveyed mobile-node locations.

```python
from pathlib import Path

from _config import Nodes, Position, SimConfig
from omegaconf import OmegaConf

study = Path(__file__).resolve().parent
config = SimConfig.from_yaml_file(str(study / "base-ran.yaml"))
config.set_simulation_id("legget-manual-u2-s41")
config.set_seed(41)
config.set_num_batches(1)
config.set_timeline(slots_per_batch=100, realizations_per_slot=1)
config.set_num_procedural_ues(0)

# The base must have no GPX sources or inherited procedural spawn zones.
for ue_id in list(config.get_ue_ids()):
    config.remove_ue(ue_id)

locations = [(45.3404552, -75.9111420), (45.3410000, -75.9120000)]
for ue_id, (lat, lon) in enumerate(locations, start=1):
    ue = Nodes.create_ue(ue_id=ue_id, radiated_power_dbm=26.0)
    ue.set_manual(True)
    ue.add_waypoint(Position.georef(lat, lon), speed=0.0)
    config.add_ue(ue)

config.set_ues_height(height_m=1.5)
OmegaConf.save(config.to_dict(), str(study / "legget-manual-u2-s41.yaml"))
```

Run `python studies/legget/make_manual.py`, inspect the emitted native YAML, then use the [runbook command](aodt.md#6-create-and-run-a-native-scenario). This adapts NVIDIA's [UE construction example](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/examples/example_client_yaml_config.py) and [editable-config API](https://docs.nvidia.com/aerial/aodt/api/config).

Use `Position.georef(latitude, longitude)` for initial placement. Check actual terrain projection and UE height in the saved result/viewer; positions over building footprints need deliberate treatment. Avoid transferring Sionna local metre coordinates without a tested transform.

### Density sweeps with procedural UEs

For a random outdoor case, replace the `locations` loop in the previous script with the following, change the simulation ID/output filename, and start from a base with no inherited spawn zones:

```python
config.add_spawn_zone([
    Position.georef(45.336, -75.917),  # southwest
    Position.georef(45.336, -75.905),  # southeast
    Position.georef(45.345, -75.905),  # northeast
    Position.georef(45.345, -75.917),  # northwest
])
config.set_num_procedural_ues(24)
config.set_perc_indoor_procedural_ues(0)
config.set_ue_speed(min_speed=0.0, max_speed=0.0)
```

Use a **convex counter-clockwise polygon of 2D points** inside the scene. Indoor percentage is on a **0–100** scale. Test 12/24/60/120 UEs and seeds 41/42 to mirror the current campaign's density design; verify the worker's realized UE count. [Placement API](https://docs.nvidia.com/aerial/aodt/api/config), [procedural RAN example](https://docs.nvidia.com/aerial/aodt/ran-simulations).

For fair condition comparisons, preserve the exact realized positions and IDs. Reusing a seed is helpful, but changing scene, mobility, or sampling settings can alter the realization. Confirm matching exported UE/route records before pairing results. Report UE density per spawn-zone area as well as total UE count.

### Walking paths and measured routes

For manual motion, add several same-dimension waypoints with nonzero `speed` in m/s and optional `pause_duration` in seconds. Use a new simulation ID and enough slots to observe the route. For a worker-readable GPX measurement route, replace manual/procedural UE construction with:

```python
config.set_num_procedural_ues(0)
config.add_ues_from_gpx(
    gpx_src="/data/measurements/legget-route.gpx",
    ue_ids=[1],
    use_pathfinding=False,
)
```

Ensure previous UE/source definitions are removed and preserve measured sample ordering. Validate location/time alignment before fitting. Vehicle/SUMO mobility is an experimental geometry/mobility option; moving vehicles are not automatically UE traffic sources, and channel motion alone does not demonstrate a complete handover procedure. [Mobility behavior](https://docs.nvidia.com/aerial/aodt/intro-mobility-model), [mobility limitations](https://docs.nvidia.com/aerial/aodt/limitations).

## 4. Change scheduler and radio conditions

Advanced RAN settings live in the worker's `config_ran.json`, mounted into the simulation backend. Relevant keys in the current [worker configuration](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/worker/config_ran.json) include:

| Key | Experiment use |
| --- | --- |
| `Scheduler Mode` | PF/RR scheduler comparison, using values supported by the deployed release. |
| `Max scheduled UEs per TTI - dl`, `... - ul` | Simultaneously scheduled UE limits. |
| `TDD patterns`, `Simulation pattern` | Downlink/uplink airtime pattern selection. |
| `UE noise figure`, `gNB noise figure` | Receiver-condition study. |
| `DL HARQ enabled`, `UL HARQ enabled` | HARQ comparison. |
| `MCS selection mode`, `MAC CSI`, `Beamformers CSI` | Link-adaptation and channel-information assumptions. |

Finish active jobs before changing this shared file. Save the full original and each variant, retain all unrelated keys, and restart/reload the worker as required by its release. Validate the **exported `ran_config`** for each run. The default JSON and prose documentation can differ; record actual effective settings rather than assuming a universal noise figure or scheduler configuration. [Batched-mode settings](https://docs.nvidia.com/aerial/aodt/batched-mode).

PF balances achievable rate against served-throughput history; RR rotates scheduling opportunities. Neither label establishes application priority or a latency guarantee. A lower BLER target is a link-reliability choice, not a service-priority setting.

## 5. Experiments to start with

These are proposed AODT studies, separate from the already scheduled Sionna campaign:

| Study | Controlled change | Compare |
| --- | --- | --- |
| Density | 12/24/60/120 UEs; fixed area/deployment, paired seeds. | UE and aggregate radio rate, lower-tail rate, allocation, fairness, failures. |
| Placement | Fixed outdoor, cell-edge cluster, wider-area random placement. | Coverage versus capacity, poor-service fraction, serving RU. |
| Scheduler | PF versus RR with identical UEs and radio settings. | Allocation, zero-service UEs, fairness, lower-tail rate. |
| Receiver noise | Baseline versus a documented higher noise figure. | SINR, MCS, decoding failure, DL/UL rate. |
| Airtime | Supported balanced versus DL-heavy TDD patterns. | DL and UL separately, normalized over the full observation. |
| Reliability | Supported HARQ/BLER-target variants. | Failure/retransmission counts and radio rate. |
| Mobility | Static versus walking/GPX path with sufficient duration. | Channel evolution and service along the route. |
| MIMO/beamforming | Supported RU panel/CSI/beamformer alternatives. | Layers, SINR, capacity; distinguish changed hardware assumptions. |
| EM convergence | Increased ray/path/interactions budget on a fixed scene. | Channel/power stability, runtime, memory, storage. |
| Physical calibration | Baseline versus independently fitted AODT settings. | Held-out received-power errors and downstream radio sensitivity. |

A useful first RAN sweep is **4 densities × 2 seeds × 4 conditions = 32 runs**: baseline PF, RR, higher receiver noise, and a supported alternate TDD pattern. First validate one small case per condition. Change one factor at a time, keep 100 MHz fixed, and explicitly archive each worker configuration. Increase seeds for statistical conclusions; two seeds are an exploratory comparison.

Our SYS mixed/light/overload profiles describe finite packet arrivals. Do not translate them into invented AODT YAML keys such as `offered_mbps` or `core_latency_ms`. Establish the RAN example's offered-load assumptions and implement/instrument an arrival/queue integration if those traffic studies are required. A full-buffer radio capacity study and a finite-load queue-delay study answer different questions.

Likewise, a 6G research experiment needs an explicit hypothesis, supported PHY/channel configuration, and validation target. AODT's research positioning does not establish a complete standardized 6G network stack.

## 6. Schedule a repeatable experiment sweep

Use this order: **asset/deployment checks → EM baseline → fresh mobile-node calibration and held-out checks → RAN smoke → density/condition sweep → result verification**. Freeze the scene, measurements, fit, software/images, numerical budgets, and output schema before the sweep.

The existing `ottawa-rt prepare-campaign/run-campaign` commands are Sionna-specific. A simple separate AODT schedule is a reviewed `run-list.txt`, one absolute native YAML path per line, with unique `db.sim_id` values. Keep secret-bearing files locally under the ignored `data/experiments/` tree; version sanitized templates and manifests separately.

For example, create the list after generating the configurations:

```text
/srv/aodt/client/studies/legget/legget-pf-u12-s41.yaml
/srv/aodt/client/studies/legget/legget-pf-u24-s41.yaml
/srv/aodt/client/studies/legget/legget-rr-u12-s41.yaml
```

**Group the list by effective worker configuration.** Run the PF group to completion and verify it, then switch/archive/reload the worker's scheduler settings before running the RR group. A YAML filename does not select a worker-wide scheduler.

The following portable Python recipe serializes one such group. Save it locally as `run_aodt_list.py`; run it with the AODT Python environment. Set `AODT_CLIENT_DIR`, `AODT_SERVER`, and `AODT_RESULTS_DIR` to the client checkout, worker gRPC address, and an empty output directory. It supplies absolute build paths because each job runs in a separate logging directory.

```python
import os
from pathlib import Path
import subprocess
import sys

import yaml

client = Path(os.environ["AODT_CLIENT_DIR"]).resolve()
results = Path(os.environ["AODT_RESULTS_DIR"]).resolve()
server = os.environ["AODT_SERVER"]
results.mkdir(parents=True, exist_ok=True)
paths = [Path(line.strip()).resolve()
         for line in Path(sys.argv[1]).read_text().splitlines()
         if line.strip()]
jobs = []
seen = set()
for path in paths:
    sim_id = str(yaml.safe_load(path.read_text())["db"]["sim_id"])
    if not sim_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                         for c in sim_id):
        raise ValueError(f"Unsafe simulation ID: {sim_id}")
    if sim_id in seen or (results / sim_id).exists():
        raise ValueError(f"Duplicate ID or existing results: {sim_id}")
    seen.add(sim_id)
    jobs.append((sim_id, path))

build = client / "build"
suffix = "Release" if sys.platform == "win32" else ""
env = os.environ.copy()
env["PYTHONPATH"] = os.pathsep.join([
    str(build / suffix), str(build / "config" / suffix),
    env.get("PYTHONPATH", ""),
])
for sim_id, path in jobs:
    job_dir = results / sim_id
    job_dir.mkdir()
    command = [sys.executable, str(client / "examples" / "example_full_sim.py"),
               "--server_address", server, "--import_option", "file",
               "--yaml_file", str(path)]
    with (job_dir / "client.log").open("w") as log:
        subprocess.run(command, cwd=job_dir, env=env, stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    (job_dir / "client-finished.txt").write_text("Verify exported results before accepting.\n")
```

Linux/WSL invocation, after activating the client environment:

```bash
export AODT_CLIENT_DIR=/srv/aodt/client
export AODT_SERVER=WORKER_HOST:50051
export AODT_RESULTS_DIR=/srv/experiments/legget-pf-20261002
python run_aodt_list.py run-list.txt
```

The recipe stops on a client failure and refuses existing result directories. It is **not** a crash-resumable supervisor, an exclusive worker lock, or a backend-completion verifier. Run only one controller per worker. After interruption, inspect client/server state and storage before recovery; use a new attempt ID and an explicit remaining-run list after resolving the cause. Confirm each unique ID is also unused in the database/catalog before submission. [Underlying full-run client](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/examples/example_full_sim.py).

For unattended execution, wrap the validated group with your host's job scheduler and direct it to the same pinned files. If jobs share a worker, reserve it for that schedule. Do not change resolution, ray budget, calibration, or other scientific settings to make a failed run finish under the same ID.

For each result, retain native YAML/hash, scene/input hashes, software commit/image digest, effective `config_ran.json`, seed/UE identities, measurement/fit/split IDs, client and worker logs, completed slots/batches, catalog/table references, and metric definitions. Keep copies of secret-bearing inputs in protected storage and publish redacted manifests.

## 7. Metrics, resource allocation, and priority

AODT RAN performs MAC resource allocation and link adaptation. It exports per-link telemetry and effective radio configuration; allocation depends on scheduler/channel/radio settings, not merely UE placement. In the current result schema, telemetry includes UE/RU, direction, slot, PRB allocation, MCS, layers, TBS, decoding outcome, and SINR. `tbs` is **bytes** and `scs` is **Hz**. [Result schemas](https://docs.nvidia.com/aerial/aodt/results-schemas).

| Quantity | Analysis to report |
| --- | --- |
| Allocation | For a scheduled row, occupied allocation is `nPrb × 12 × scs / 1e6` MHz. Include zero allocation for unscheduled slots in a time average. |
| Radio rate | Successful TBS bytes × 8 / full simulated observation / 1e6. Separate DL/UL and UE/RU totals; check HARQ accounting before claiming unique payload delivery. |
| Failure rate | Failed transport-block attempts / all attempts; state direction, time window, and grouping. |
| Channel/link quality | SINR distributions, MCS/layers, channel/path outputs when enabled. |
| Fairness | Compute Jain fairness and lower-tail rates over all configured UEs, including UEs receiving no service. |
| Calibration | Held-out power errors, supported frequencies, sample counts, and parameter assumptions. |
| Runtime | Measured wall time, memory, output size, and simulated radio time; these have different meanings. |

These formulas are analysis choices, not a claim that the SDK's packaged metric scripts implement them identically. Account for retransmissions and distinguish full-horizon rate from scheduled-slot rate. For a multi-batch result, report per-realization variation before pooling; do not mistake 14 channel samples in a slot for 14 elapsed slots.

**Application goodput, packet queue delay, and end-to-end latency need packet-level evidence.** The radio telemetry alone does not supply arrival timestamps, application delivery, core-network delay, or TCP behavior. Add explicit traffic instrumentation/protocol integration before reporting those measurements. Our implemented SYS simulator's finite queues and configured core-delay estimate remain available through [its own guide](network-simulation.md).

**UE priority is a separate design choice.** PF/RR scheduling and per-UE BLER settings do not establish video/sensor priority, 5QI policies, guaranteed bit rates, or packet deadlines. To study priority, identify the supported QoS/traffic hooks or implement a policy, log arrivals/queues/grants/delivery, and compare service classes under congestion. Do not infer a priority rule from a profile name.

Verify exported configuration/UE records alongside telemetry. Output powers and positions require their table's units and scene scale; route coordinates are not automatically Sionna local metres or height above ground. [Export schemas](https://docs.nvidia.com/aerial/aodt/results-schemas).

Waveform dumping can support PHY/debugging studies, but generates substantial storage. Follow the [waveform guide](https://docs.nvidia.com/aerial/aodt/waveform-dumping) and validate a short capture first. Enable full paths/CIR/CFR exports only where the experiment or calibration needs them.

Accept a sweep only after every expected simulation has the intended effective settings, complete slots/batches, readable required tables, correct UE counts, finite/credible values, and pinned calibration evidence. Publish density/condition comparisons with units, paired seeds, uncertainty, and material limitations. For Sionna/AODT comparison, match the supported carrier, placement, channels/deployment, and load assumptions explicitly; differences in PHY fidelity and antenna models can dominate the result.
