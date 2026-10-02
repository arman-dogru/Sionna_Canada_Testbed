# AODT configuration reference

This is the AODT counterpart to [the Sionna configuration reference](configuration.md). It describes **1.5.1** interfaces checked on **October 2, 2026**, including the native sample and worker settings at NVIDIA source commit `b1bf1ee19bfefcc46fa941f136582a5e38d197ff`.

Start with [the beginner run guide](../RUN_AODT_GUIDE_FOR_NON_TECHNICALS.md) for a first run, [the setup runbook](aodt.md) for installation, and [the experiment recipes](aodt-experiments.md) for UE builders and scheduling. These are documentation for a separate AODT deployment; runtime execution and Ottawa scene conversion have not been validated in this checkout.

## Which file controls what?

| File | Scope | Typical edit |
| --- | --- | --- |
| Complete native scenario YAML | One simulation. | Scene, deployment, UEs, seed, timeline, solver/export settings. |
| Builder asset configuration | Defaults/assets when constructing a scenario. | Panel/object default assets and paths. |
| `worker/config_ran.json` | Shared worker RAN behavior. | PF/RR, TDD, HARQ, noise, MCS/CSI assumptions. |
| Worker Compose/release manifest | Deployment. | Images, GPU access, service ports, persistent mounts. |
| Viewer settings | Browser access and display. | Reachable catalog and S3 endpoints. |
| Study manifest | Scientific provenance. | Input/software hashes, calibration, realized UEs, effective radio configuration. |

The native YAML uses `db`, `gis`, and `sim`, with an additional `cal` section for calibration. Sionna project/traffic/campaign YAMLs have a different schema. Submit AODT YAML through the AODT client; the `ottawa-rt` CLI does not read it.

Copy the release's [complete RAN sample](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/b1bf1ee19bfefcc46fa941f136582a5e38d197ff/client/tests/assets/quickstart_batched_ran.yaml), or generate a complete configuration with `_config.SimConfig`. The fragments below illustrate fields inside that complete file; they are not standalone scenarios. [Native YAML workflow](https://docs.nvidia.com/aerial/aodt/configuring-sim-yaml).

## `db`: identity, storage, and exported results

| Path | Meaning and operator choice |
| --- | --- |
| `db.sim_id` | Unique run identity. Use a new ID for a changed experiment or an explicitly recovered attempt. |
| `db.db_host`, `db.db_port`, `db.db_author` | Worker database connection/author. The sample uses internal service names. |
| `db.opt_in_tables` | Requested output families, such as telemetry, CIRs, CFRs, or ray paths. Select what the analysis needs. |
| `db.opt_in_tables_options.raypaths` | Ray-path detail; calibration workflows require the prescribed full-ray export. |
| `db.s3_config` | Asset storage bucket/provider/endpoint and credentials. |
| `db.parquet_export.s3_configs` | Result export destinations. Check these separately from asset storage. |
| `db.parquet_export.iceberg` | Catalog configuration for exported tables. |
| `db.parquet_export.timesteps_per_file` | File grouping; affects output management, not slot duration. |
| `db.parquet_export.verify_exports` | Sample export-verification option. Inspect its result as part of run acceptance. |

Distinguish endpoints reachable by the worker from those reachable by the client/browser. In current Compose, gRPC is published on 50051, MinIO S3 normally on 9000, its console on 9001, and the catalog on 19120; deployed overrides take precedence. A worker's `http://minio:9000` is not a remote browser address. [Endpoint details](aodt.md#3-connect-a-client-and-identify-the-endpoints).

Keep credentials in protected local configurations and publish redacted templates/manifests. Prefer the repository's ignored runtime tree for secret-bearing study files. File-import mode uses the YAML's endpoints as written.

## `gis`: the prepared scene

```yaml
gis:
  scene:
    scene_url: test_data/maps/tokyo
```

`scene_url` names prepared scene assets. Replacing it does not move Tokyo's RU/UE deployment into Ottawa: change and verify the deployment too. Building/terrain/material changes need new assets and propagation validation.

For Ottawa, preserve the original geographic snapshots, source hashes, context, datum/units, anchor, and tested coordinate conversion. The Sionna scene XML and coverage arrays are not a documented drop-in import. See [scene preparation and extent constraints](aodt.md#5-prepare-an-ottawa-scene).

## `sim`: DUs, RUs, and antenna panels

Native object groups use `default` asset paths, `add` entries, and `update` entries. Updates associate `attributes` with their `ids`. Retain those relationships when editing a complete sample; using the builder reduces reference mistakes.

| Native field/group | Meaning | Unit or constraint |
| --- | --- | --- |
| `sim.DUs` | Distributed-unit objects used by RUs. | Create a referenced DU first. |
| `aerial_du_reference_freq` | DU reference carrier. | MHz. |
| `aerial_du_subcarrier_spacing` | Subcarrier spacing. | kHz; documented RAN uses 30. |
| `aerial_du_max_channel_bandwidth` | Carrier bandwidth. | MHz; documented RAN uses 100. |
| `aerial_du_fft_size` | FFT size. | Preserve a supported combination from the sample/release. |
| `sim.RUs` / `position` | Radio-unit location. | Georeferenced or tested scene coordinates. |
| `aerial_gnb_du_id` | RU's associated DU. | Existing DU ID. |
| `aerial_gnb_carrier_freq` | RU carrier. | MHz; align with deployment and panel. |
| `aerial_gnb_height` | RU height above ground. | Metres. |
| `aerial_gnb_radiated_power` | RU radiated power. | dBm; reconcile measured/ISED power interpretation. |
| `aerial_gnb_mech_azimuth`, `aerial_gnb_mech_tilt` | Antenna orientation. | Degrees; validate geographic conventions. |
| `aerial_gnb_panel_type` | Referenced panel. | Existing panel ID. |
| `sim.Panels` | Panel geometry, elements, polarization, and reference frequency. | Maintain compatible antenna and frequency definitions. |

Changing a DU's frequency does not automatically change its RUs; update the associated RU and panel frequencies deliberately. For RAN, the documented arrangements are four- or 64-antenna RUs and four-antenna UEs. Do not interpret a builder setter as proof that an arbitrary bandwidth/antenna arrangement is supported by RAN. [Builder API](https://docs.nvidia.com/aerial/aodt/api/config), [RAN limits](https://docs.nvidia.com/aerial/aodt/limitations).

## UEs and placement

| Choice | Native/builder control | Unit or rule |
| --- | --- | --- |
| Explicit UE identity | `sim.UEs.add[].id` / `Nodes.create_ue()` | Unique ID; retain it across paired conditions. |
| Fixed/manual UE | `aerial_ue_manual` / `ue.set_manual(True)` | Define at least one waypoint. |
| Waypoint | `waypoints[].pos` / `Position.georef(lat, lon)` | Latitude first, longitude second in the builder. |
| Motion | Waypoint `speed`, `pause_duration` | m/s and seconds; speed zero gives a stationary waypoint. |
| UE height | `height_m` / `config.set_ues_height()` | Metres above terrain; inspect the realized location. |
| UE transmit power | `aerial_ue_radiated_power` | dBm; especially relevant to UL. |
| BLER target | `aerial_ue_bler_target` / `ue.set_bler_target()` | Fraction, for example 0.1; link reliability, not application priority. |
| Random population | `config.set_num_procedural_ues()` | Verify generated count; remove inherited explicit UEs if replacing them. |
| Spawn region | `config.add_spawn_zone()` | Convex counter-clockwise polygon of 2D points inside the scene. |
| Indoor share | `config.set_perc_indoor_procedural_ues()` | Percentage on a 0–100 scale. |
| Random speed range | `config.set_ue_speed()` | Minimum/maximum in m/s. |
| Measured route | `config.add_ues_from_gpx()` | File must be readable by the worker. Disable pathfinding when preserving measured positions. |

For a fixed example, adapt a complete reviewed base as follows:

```python
from pathlib import Path

from _config import Nodes, Position, SimConfig
from omegaconf import OmegaConf

output = Path("studies/legget/legget-fixed-u2-s41-r1.yaml")
if output.exists():
    raise FileExistsError(output)
config = SimConfig.from_yaml_file("studies/legget/base-ran.yaml")
config.set_simulation_id("legget-fixed-u2-s41-r1")
config.set_seed(41)
config.set_num_batches(1)
config.set_timeline(slots_per_batch=100, realizations_per_slot=1)
config.set_num_procedural_ues(0)
for ue_id in list(config.get_ue_ids()):
    config.remove_ue(ue_id)
for ue_id, (lat, lon) in enumerate([
    (45.3404552, -75.9111420),
    (45.3410000, -75.9120000),
], start=1):
    ue = Nodes.create_ue(ue_id=ue_id, radiated_power_dbm=26.0)
    ue.set_manual(True)
    ue.add_waypoint(Position.georef(lat, lon), speed=0.0)
    config.add_ue(ue)
config.set_ues_height(height_m=1.5)
OmegaConf.save(config.to_dict(), str(output))
```

The base must already describe validated Ottawa assets/radios, with inherited GPX/spawn sources removed. These illustrative coordinates are not surveyed measurement samples. Inspect outdoor placement before a scientific run. The complete [UE recipes](aodt-experiments.md#3-place-ues) cover procedural polygons, walking paths, and GPX sources.

## Scenario mode, timeline, and numerical budget

These fields belong under `sim.Scenario.update[].attributes`:

```yaml
sim_is_full: true
sim_simulation_mode: 1
sim_batches: 1
sim_slots_per_batch: 100
sim_samples_per_slot: 1
sim_is_seeded: true
sim_seed: 41
```

| Field | Meaning and tradeoff |
| --- | --- |
| `sim_is_full` | `true` enables RAN; `false` selects propagation without the RAN stack. |
| `sim_simulation_mode` | `1` uses slots; `0` uses duration/interval. RAN requires the slot timeline. |
| `sim_batches` | Number of realizations. Analyze their variation before pooling. |
| `sim_slots_per_batch` | Radio observation per batch. Longer observations require more work/output. |
| `sim_samples_per_slot` | Supported RAN channel sampling of 1 or 14; does not multiply elapsed slot time. |
| `sim_duration`, `sim_interval` | Seconds for EM duration/interval mode; do not mix timeline styles. |
| `sim_is_seeded`, `sim_seed` | Reproducible randomization controls; still verify realized positions. |
| `sim_em_rays` | Rays in thousands: 500 represents 500,000 rays. Increase only under a new documented numerical configuration. |
| `sim_em_interactions` | Interaction depth; higher budgets need convergence/runtime checks. |
| `sim_em_max_num_paths_per_ant_pair` | Path cap; can change retained channel detail and resource needs. |

At 30 kHz spacing, one slot lasts 0.5 ms: 100 slots = 0.05 seconds and 10,000 slots = five seconds per batch. Wall time is measured separately. Batches are separate realizations; ten 1,000-slot batches are not automatically one continuous five-second packet trace. Manual/GPX routes repeat across batches, while procedural realization can change. [Timeline workflow](https://docs.nvidia.com/aerial/aodt/batched-mode), [mobility behavior](https://docs.nvidia.com/aerial/aodt/intro-mobility-model).

## `worker/config_ran.json`: scheduler and radio conditions

Edit the full JSON on the worker and archive each variant. It is shared by jobs using that worker; a YAML run name does not apply these settings. The table records the checked source snapshot, not guaranteed defaults for every deployment. Validate the run's exported `ran_config`.

| Exact key | Checked sample value | Experiment meaning |
| --- | --- | --- |
| `Scheduler Mode` | `PF` | Compare supported PF/RR allocation with identical UEs. |
| `Max scheduled UEs per TTI - dl` / `... - ul` | 6 / 6 | Simultaneously scheduled users per cell/direction. Respect release limits. |
| `TDD patterns` | Pattern dictionary. | Defines DL (`D`), UL (`U`), and special (`S`) slots. |
| `Simulation pattern` | 1 | Selects a dictionary entry. Inspect its actual pattern. |
| `UE noise figure` / `gNB noise figure` | 0.5 / 0.5 | Receiver noise in dB. These are not the Sionna profile's 7 dB assumption. |
| `DL HARQ enabled` / `UL HARQ enabled` | 1 / 1 | Retransmission behavior; account for HARQ in rate/failure analysis. |
| `MCS selection mode` | `OLLA` | Link-adaptation behavior. |
| `MAC CSI` / `Beamformers CSI` | `CFR` / `CFR` | Channel-information assumptions for scheduling/beamforming. |
| `Fixed UE layers - dl` / `... - ul` | Fixed mode disabled. | Rank/layer experiments within supported configurations. |

For a PF/RR comparison, copy the full JSON, change only `Scheduler Mode` to the deployed release's supported `RR` value, finish active jobs, reload/restart the worker as required, and run a small verification case. Preserve each effective JSON/hash. Do the same when changing TDD, HARQ, noise, or CSI assumptions. [Checked worker source](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/b1bf1ee19bfefcc46fa941f136582a5e38d197ff/worker/config_ran.json).

PF/RR and BLER targets do not create video/sensor priority, 5QI policy, guaranteed rates, or packet deadlines. These require an explicit policy and traffic evidence.

## Calibration and measured inputs

An AODT calibration scenario adds a `cal` section to a matched EM configuration. Establish measured RU/UE links and full-ray-path exports first, then configure the supported targets, measurement files, timeline, and output through the builder. Follow [the AODT calibration runbook](aodt.md#7-calibrate-against-the-mobile-node-data) for panel/link restrictions and worker-mounted file paths.

Preserve the mobile-node source hashes, matching decisions, and spatial train/validation/test splits. Refit for AODT and report held-out MAE/RMSE/bias plus frequency support. A Sionna fitted-power JSON cannot be assumed compatible with AODT. Received-power calibration does not calibrate traffic demand, packet latency, or service priority.

## What must be repeated after a change?

| Change | Required work before accepting new results |
| --- | --- |
| Buildings, terrain, coordinates, or materials. | Prepare/verify new assets, repeat propagation and calibration validation, then RAN. |
| RU location, height, orientation, power, carrier, or panel. | Check references/units, repeat propagation, reassess calibration, then RAN. |
| UE count, placement, route, speed, height, or panel. | Generate a new scenario, check realized UEs, rerun the affected EM/RAN observation. |
| Seed, rays, interactions, path cap, or channel sampling. | New numerical scenario; repeat propagation/RAN and any affected convergence checks. |
| Slots/batches. | New observation and run ID; verify completed horizon and rate denominators. |
| Scheduler, TDD, HARQ, noise, MCS, or CSI. | Archive/reload the worker variant, verify effective settings, rerun RAN. |
| Calibration measurements or fitted physical settings. | Preserve new evidence, refit/revalidate, regenerate affected channels and RAN results. |
| Requested tables/export configuration. | Verify existing evidence suffices; rerun if required outputs were not saved. |
| Viewer display/endpoints only. | Reconnect/reload; no physical rerun if the saved outputs are unchanged. |
| Analysis formula or aggregation only. | Recompute from sufficient saved telemetry; retain formula/version and original outputs. |

Use a new simulation ID whenever scientific inputs change. Save native YAML/hash, exact software/images, scene/assets, effective worker JSON, realized UE identities/positions, calibration/splits, completed horizon, logs, and result catalog references. See [sweep scheduling](aodt-experiments.md#6-schedule-a-repeatable-experiment-sweep).

## Metrics and scope

Radio telemetry supports PRB allocation, MCS/layers, SINR, decoding results, and rate calculations with an explicit observation horizon. At 30 kHz, `nPrb × 12 × 30,000 / 1e6` gives occupied allocation in MHz for a scheduled row; include unscheduled slots as zero in a time average. The documented TBS unit is bytes. Account for retransmissions before claiming unique payload goodput.

Packet queue delay and application/end-to-end latency require arrival and delivery timestamps plus the relevant protocol/network model. Do not introduce unsupported native fields such as `offered_mbps` or `core_latency_ms` to mimic the SYS traffic model. [Result schema](https://docs.nvidia.com/aerial/aodt/results-schemas), [analysis and priority guidance](aodt-experiments.md#7-metrics-resource-allocation-and-priority).
