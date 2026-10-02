# UE placement and network traffic

Checked on 2026-10-01. The implementation uses NVIDIA **Sionna RT 2.1.0 + Sionna SYS 2.1.0** on the existing Ottawa scenes. It traces channels at outdoor UE coordinates, schedules downlink resources, selects NR MCS, simulates transport-block decoding, and feeds finite packet queues. Results include UE and aggregate goodput, allocated bandwidth, queue delay, estimated end-to-end delay, packet loss, backlog, and fairness.

## NVIDIA tool selection

**Aerial Omniverse Digital Twin (AODT) remains suitable for a future full RAN digital twin, but Sionna SYS is the working choice for this Windows workstation.**

The current AODT documentation is **1.5.1**. Its RAN mode combines EM ray tracing with the 5G L1/L2 transmit/receive stack and exposes per-UE BLER, throughput, and PF telemetry. [NVIDIA RAN quickstart](https://docs.nvidia.com/aerial/aodt/ran-simulations).

Its current documented RAN mode is limited to 100 MHz, 273 PRBs and 30 kHz spacing, with specified antenna configurations. Our SYS experiment sweep can compare 20/40 MHz within its stated abstraction. Check the release-specific constraints before migrating the sweep to AODT. [Current RAN limitations](https://docs.nvidia.com/aerial/aodt/limitations).

The worker prerequisites specify Ubuntu 22.04, Docker Compose v2, NVIDIA Container Toolkit, NGC access, and supported GPUs including RTX 6000 Blackwell, GB10, RTX 6000 Ada, L40 and L40S. This machine runs Windows with two RTX 3070s, each with 8 GB VRAM, and has no Docker executable available. It is outside that supported worker setup. A remote worker could support a Windows client later. [Current prerequisites](https://docs.nvidia.com/aerial/aodt/prerequisites), [worker deployment](https://docs.nvidia.com/aerial/aodt/worker-installation).

Older 1.4.1 documentation explicitly specified 48 GB for the backend and 12 GB for the frontend. Those numbers describe the archived release; the current 1.5.1 prerequisites should guide new deployments. [Archived requirements](https://docs.nvidia.com/aerial/aerial-dt/archive/1.4.1/text/installation.html).

Sionna SYS is NVIDIA's open-source system-level simulator, with PHY abstraction, link adaptation, and scheduling. NVIDIA documents an integration with Sionna RT. That lets this repository retain its public-data terrain, building geometry, material policies, sector normalization, and coordinate conventions. [SYS overview](https://nvlabs.github.io/sionna/sys/index.html), [official SYS/RT example](https://nvlabs.github.io/sionna/v2.1.0/sys/tutorials/notebooks/SYS_Meets_RT.html).

Other NVIDIA options target a different next step: Aerial CUDA-Accelerated RAN is a software-defined RAN stack, while Sionna Research Kit provides an OpenAirInterface-based research platform for radio experimentation. For full protocol or over-the-air experiments, evaluate those alongside the hardware and core-network requirements. [NVIDIA AI Aerial portfolio](https://developer.nvidia.com/topics/telecommunications/ai-aerial).

No API key is required for the implemented Sionna pipeline. Cesium credentials continue to support the existing visualization. Installing an AODT client alone would not provide its worker or a working RAN simulation.

## Install and run on this checkout

Use PowerShell in `D:\Projects\Sionna_RT_OTTAWA`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[network,dev]"
.\.venv\Scripts\python.exe -m ottawa_rt.cli simulate-network `
  --config config/demo-4km-5m.yaml `
  --scenario config/network-ottawa.yaml
```

The supplied scenario has already been run locally. Choose a new `name` in the YAML for a new experiment. To deliberately rerun the same name:

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli simulate-network --overwrite
```

The default uses the completed `demo-4km-5m-multiband-20260918` run to identify the scene and transmitters, at the 3505 MHz RF group. It places 24 outdoor UEs in a 1 km square around the local origin: eight video streams, eight Poisson interactive flows, and eight sensors. The generated channels use the actual NRCan terrain and public building meshes.

The RT trace uses the CUDA polarized Mitsuba variant on an RTX 3070. Sionna SYS was tested with PyTorch 2.11.0 CPU; CPU is adequate for these small traffic experiments. `device: auto` selects CUDA for SYS when a CUDA PyTorch wheel is installed, otherwise CPU. `device: cuda` fails explicitly if unavailable. The SYS device setting does not change the RT runtime's device selection. GPU SYS execution was not validated in this Windows CPU-wheel environment.

On another host, install the same extras and copy/rebuild the `data/` tree. The generated Ottawa scenes and normalized transmitter records are required and are excluded from Git.

## Scenario settings

`config/network-ottawa.yaml` is a network scenario, separate from the existing project/RF YAML supplied with `--config`.

| Setting | Meaning |
| --- | --- |
| `name`, `run_id` | Output experiment name and source RT run. Safe identifiers only. |
| `frequency_mhz` | A frequency group present in the source run, bucketed using the existing 5 MHz convention. |
| `channel_source` | `paths` traces exact UE CIRs; `radio_map` samples saved raw per-sector grids. |
| `ray_samples_per_src`, `ray_max_depth` | Direct UE path solver budget. Defaults: 100,000 rays/source and depth four. |
| `operator` | Optional serving-cell filter. Other operators still contribute interference. |
| `calibrated` | Apply the source run's fitted per-sector power corrections; fail if unavailable. |
| `calibration_model_path` | Optional project-relative path to a pinned fit. Requires `calibrated: true`; the model must match the scene/model version. Omit to use compatible `latest.json`. |
| `duration_s`, `seed`, `device` | Duration, deterministic experiment seed, and SYS compute device. Duration must contain whole slots. |
| `bandwidth_mhz`, `numerology` | Hypothetical NR carrier width and 15/30 kHz spacing (`0`/`1`). |
| `data_symbols` | Payload OFDM symbols per slot, after assumed overhead. Default 11. |
| `downlink_fraction` | Deterministic downlink slot fraction. Default 0.75. Other slots do not serve downlink queues. |
| `bler_target`, `pf_beta` | ILLA target and PF averaging discount. |
| `noise_figure_db` | Receiver noise figure used with configured carrier bandwidth. |
| `core_latency_ms` | Constant added to delivered-packet radio/queue delay for an estimated E2E value. |
| `placement_width_m`, `center_x_m`, `center_y_m` | Outdoor placement square in the source scene's local UTM coordinates. |
| `profiles` | UE counts and traffic definitions. Up to 500 UEs total. |
| `ues` | Optional exact UE positions instead of seeded random placement. |

NR resource-block counts follow [TS 38.104 Table 5.3.2-1](https://www.etsi.org/deliver/etsi_ts/138100_138199/138104/16.20.00_60/ts_138104v162000p.pdf). For example, a 20 MHz carrier at 30 kHz spacing has 51 RBs and 18.36 MHz occupied subcarrier bandwidth. Configured channel bandwidth, occupied bandwidth, and average allocated UE bandwidth are separate output fields.

Every profile accepts `name`, `count`, `arrival`, `offered_mbps`, `packet_bytes`, and `buffer_packets`. Arrivals can be `cbr`, `poisson`, or `full_buffer`. Full-buffer mode keeps the configured packet buffer supplied; its offered rate is determined by refill demand rather than `offered_mbps`.

## Place UEs at specific coordinates

Supply one UE for each counted profile member. Example standalone scenario:

```yaml
name: legget-two-ues
run_id: demo-4km-5m-multiband-20260918
frequency_mhz: 3505
duration_s: 2
placement_width_m: 1000
profiles:
  - name: video
    count: 2
    offered_mbps: 5
ues:
  - ue_id: ue-west
    profile: video
    x_m: -250
    y_m: 100
  - ue_id: ue-east
    profile: video
    x_m: 250
    y_m: 100
```

Coordinates are metres east/north of the source scene's local origin; WGS84 positions are included in results. Each UE is set at the project YAML's default receiver height above the sampled DTM. A UE outside the RT output area, inside a building footprint, or over terrain nodata is rejected. Indoor and rooftop placement are not supported by this outdoor workflow. Saved-map mode retains the map's recorded height.

## Results and browser display

Outputs are written under `data/network/<name>/`:

- `result.json`: assumptions, exact scenario, source SHA-256 hashes, runtime versions, summary, UE metrics, and 100 ms time series.
- `channels.npz`: direct CIR complex amplitudes `a`, absolute delays `tau`, reduced received powers, sector/UE identities, and trace metadata. Present for `paths` mode.
- `ues.csv`: flat UE positions, serving cells, SINR, goodput, bandwidth allocation, and packet counters.
- `ues.geojson`: locations and metrics for map integrations.

`a` has shape `[UE, 1, sector, 1, path, 1]`; `tau` has shape `[UE, sector, path]`. Different transmitter traces are zero-padded in amplitude and padded with `-1` for invalid delays. Values preserve absolute propagation delays.

After rebuilding the web application and restarting the API, select the source simulation run in the browser. The **UE performance** panel lists its traffic experiments. Selecting one displays aggregate metrics, throughput over time, a UE table, and UE markers in Planning map, Planning 3D, or Photo 3D. Green indicates positive delivered throughput; pink indicates zero throughput. Refresh reloads completed CLI experiments. The panel displays saved results; experiments are configured and launched through the CLI.

```powershell
pnpm --dir web run build
.\.venv\Scripts\python.exe -m ottawa_rt.cli serve --config config/demo-4km-5m.yaml
```

The API exposes:

- `GET /v1/network?run_id=...`
- `GET /v1/network/{name}`
- `GET /v1/network/{name}/ues`
- `GET /v1/network/{name}/csv`

## Radio resource allocation and UE priority

The current simulator uses NVIDIA Sionna SYS `PFSchedulerSUMIMO`. In each downlink slot, only UEs with queued data, a serving cell and a useful achievable rate are eligible. Resource blocks compete within each serving cell. PF balances achievable rate against exponentially averaged delivered service; `pf_beta` controls the history window. Link adaptation selects MCS to target the configured BLER, and PHY abstraction samples transport-block decoding success. Queue service, including partial packets, updates scheduler history.

Video, interactive and sensor names select arrivals, offered rates, packet sizes and buffers. They do **not** assign service-class priority. There are currently no priority weights, 5QI/QFI rules, guaranteed bit rates, packet deadlines, slicing quotas or admission-control policies. PF fairness also does not mean equal throughput or a latency guarantee. Compare Jain fairness within comparable traffic classes when offered demand differs.

AODT in RAN mode includes the 5G L1/L2 stack and MAC scheduling; EM-only mode produces propagation results. Its documented configuration includes PF and round robin, and telemetry records per-UE PRB allocation, MCS, layers and decoding outcomes. Application-specific QoS priority should not be assumed from PF/RR alone. [RAN workflow](https://docs.nvidia.com/aerial/aodt/ran-simulations), [scheduling configuration and telemetry](https://docs.nvidia.com/aerial/aodt/results-schemas).

For our pipeline, scheduling-policy research can extend the SYS adapter with weighted or deadline-aware allocation, then compare throughput, per-class delay, delivery and fairness against the current PF baseline. Those policies are not implemented by the supplied campaign.

## Scheduling multiple simulations

Use `prepare-campaign`, `run-campaign`, and `campaign-status` for dependency-ordered density/condition experiments. The [campaign guide](experiment-campaigns.md) includes foreground/background commands, recovery, result comparisons, the exact October 1 schedule and the 10 km / 1 m coverage run.

Set `calibrated: true` in individual scenario YAMLs. Pin `calibration_model_path` for a controlled comparison, because a later fit can change `latest.json`. The fit must match the source scene and model version. A new scene requires a new fit using the actual mobile-node rows rather than renaming an old calibration.

Network `result.json` now contains a `calibration` record with the fitted model ID/hash, held-out RF errors, training group counts and a support label. `frequency-group-supported` describes broad-group training support; `receiver-offset-extrapolation` marks a group with no usable training baseline. Neither label validates scheduler/traffic parameters or proves every spatial position is calibrated.

## Verified Ottawa experiments

The verification script reruns real GPU traces and SYS traffic, checks identical-seed repeatability, compares bandwidth/load, and exercises the `legget-6000m` scene:

```powershell
.\.venv\Scripts\python.exe scripts/validate-network.py
.\.venv\Scripts\python.exe -m pytest
```

Measured here on 2026-10-01, using 24 UEs over 2 seconds, 30 kHz spacing and a 75% downlink slot fraction. These initial verification cases were **uncalibrated**; keep them separate from the new mobile-node-calibrated campaign:

| Experiment | Carrier width/cell | Aggregate goodput | Radio/queue P95 | Tail-drop loss | Pending packets |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mixed video/interactive/sensor | 20 MHz | 20.4792 Mbps | 1007.688 ms | 0.344% | 6325 |
| Same traffic and UE positions | 40 MHz | 31.7424 Mbps | 728.000 ms | 0.344% | 4031 |
| One twentieth of offered rates | 20 MHz | 2.1192 Mbps | 5.500 ms | 0% | 112 |
| Ten times offered rates | 20 MHz | 29.2912 Mbps | 1338.164 ms | 78.889% | 12198 |

The baseline resolved channels for all 24 UEs across five serving sectors. It reproduced UE results and the time series exactly when rerun. A separate six-UE smoke experiment also passed on the 6 km scene. A small verification report is written to `outputs/network-verification-20261001.json`; generated reports and simulations stay outside Git.

## Interpret the metrics

Goodput counts complete delivered application packets divided by the full simulation duration. Partial transmissions remain separately accounted for until packet completion. Average allocated MHz is occupied subcarrier width weighted by granted RBs across all slots, including non-downlink slots; it is not peak speed or an operator's subscription bandwidth.

Packet loss means finite-buffer tail drops. Packets still queued at the horizon are **pending**, not delivered or silently counted as losses. PHY failures retain queued bytes for a later grant. Queue accounting checks offered bits against served bits, remaining bits, and dropped bits. Delay percentiles include delivered packets only: low delay with a large backlog or low delivery ratio does not indicate good service. The configured 5 ms core delay is an assumption, not a measured core-network result.

## Physical and protocol limits

- Static outdoor SISO channels. CIR path energies are reduced to a wideband, frequency-flat gain; frequency-selective equalization, MIMO, beam management, Doppler, mobility and handovers are not modeled.
- Directional antenna weighting matches the existing repository's per-link ISED pattern approximation. The complete per-path antenna departure-angle model remains a possible refinement.
- The selected 5 MHz RF group is treated as co-channel, with all finite non-serving sectors at full load. Carrier widths are hypothetical experiment settings with fixed total received power, not reconstructed commercial carrier plans. Load-coupled interference is not modeled.
- The scheduler uses NVIDIA PF with MCS/BLER-aware expected rates. ILLA selects table-1 PDSCH MCS; stochastic PHY abstraction determines decoded transport-block bits. Completely undecodable static links have no useful achievable rate.
- Packet buffers, fragmentation, retries and arrival timing are a local traffic model. This does not implement a complete NR MAC/HARQ/RLC/PDCP/IP/TCP stack, HARQ combining/timing, or uplink. For protocol-accurate application latency, move to a full-stack simulator or channel emulator with a 5G stack.
- Slot-batched arrivals quantize timing by up to one slot. E2E delay adds only the chosen constant core delay.
- Direct path tracing uses one transmitter per call to fit the 8 GB GPU. The candidate cap is 10,000 paths per source; inspect the saved channels and increase ray budget/depth for convergence studies. Exact repeatability is verified, but physical ray-budget convergence is not claimed.
- Saved 3.5 GHz radio maps were sparse at the sampled outdoor points: only four of 24 UE locations had finite data. Direct tracing resolved all 24, so it is the default. Missing map samples must not be interpreted as demonstrated physical coverage outages; map mode does not invent fallback powers.
- Public ISED installations, default antenna assumptions, LOD1 geometry and material uncertainties still apply. Calibration corrects powers rather than proving commercial-network capacity.
- This supports 5G/6G research experiments using an NR abstraction; it does not implement a standardized 6G air interface.
