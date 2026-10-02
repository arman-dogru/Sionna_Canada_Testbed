# Ottawa Sionna RT Cellular Digital Twin

A reproducible outdoor cellular-propagation testbed centered on **350 Legget Drive, Kanata, Ontario** (`45.340455200216, -75.91114196736`). It combines public Ottawa LOD1 buildings, NRCan HRDEM terrain, ISED terrestrial spectrum-site records, NVIDIA Sionna RT, a read-only API, and a browser map.

The canonical deployment is a pinned Python 3.11 / Ubuntu 24.04 CUDA container. Work is split into independent tile/frequency jobs with one Sionna RT process per GPU or MIG device; VRAM is never assumed to pool across devices.

## Operator documentation

- [Simulation and GUI runbook](docs/operations.md) — copy-paste workflows for Windows/WSL, dual-GPU runs, resuming, finalizing, serving the browser GUI, Cesium ion, Docker, DGX, and troubleshooting.
- [Configuration reference](docs/configuration.md) — every YAML section, supplied profiles, sizing math, rebuild requirements, and reproducibility rules.
- [UE placement and network traffic](docs/network-simulation.md) — NVIDIA tool assessment, Sionna SYS experiments, UE placement, throughput/bandwidth/latency results, and limitations.
- [Uplink and modulation experiments](docs/uplink-modulation-experiments.md) — sensor-to-BS traffic, one-minute runs, fixed/adaptive NR MCS comparisons, power/interference assumptions and scheduling.
- [Aerial CUDA-Accelerated RAN assessment](docs/aerial-cuda-ran.md) — pyAerial/cuPHY and cuMAC experiments, local hardware compatibility, proposed build validation, and integration with Sionna terrain and packet metrics.
- [AODT beginner run guide](RUN_AODT_GUIDE_FOR_NON_TECHNICALS.md) — first experiment, UE setup, three-terminal monitoring, results, and repeatable studies.
- [AODT configuration reference](docs/aodt-configuration.md) — native YAML fields, units, worker radio settings, and which stages to repeat after changes.
- [AODT setup and experiment runbook](docs/aodt.md) — supported worker/client setup, native smoke runs, Ottawa scene preparation, mobile-node calibration, viewer, and troubleshooting.
- [AODT configurations and experiments](docs/aodt-experiments.md) — fixed/random/moving UEs, radio settings, experiment designs, sequential scheduling, metrics, and resource-allocation limits.
- [Scheduling calibrated experiment campaigns](docs/experiment-campaigns.md) — density/condition sweeps, pinned mobile-node calibration, ordered execution, monitoring/resuming, and the 10 km / 1 m coverage campaign.
- [Windows RT run guide](RUN_RT_GUIDE_FOR_NON_TECHNICALS.md) and [Windows GUI run guide](RUN_GUI_GUIDE_FOR_NON_TECHNICALS.md) — instructions for the checkout at `D:\Projects\Sionna_RT_OTTAWA`.
- [Receiver measurements and calibration](#receiver-measurements-and-calibration) — measurement schema and fitting workflow.

Use the same `--config` file for scene creation, simulation, finalization, queries, and the GUI. Create a new run ID whenever any physical or numerical setting changes.

## Current build status

The checked-out workspace contains a real public-data snapshot and completed end-to-end GPU runs:

- 2 km by 2 km output centered on Legget Drive, with 2 km of surrounding scene context.
- 14,321 LOD1 buildings and 2,198,554 terrain/building triangles.
- 812 normalized mobile sector-frequency records from the ISED SMS extract; 111 records carry explicit fallback flags.
- A completed native-5 m, 4 km by 4 km demonstration run (`demo-4km-5m-multiband-20260918`) with 64 half-kilometre tiles, nine representative mobile downlink groups from 635 to 3820 MHz, and 576/576 successful dual-GPU jobs. Each stitched band is 800 by 800 receiver cells.
- One Sionna RT tile job completed on an RTX 3070 with the CUDA polarized variant: 15 transmitters on a 220 by 220 terrain-draped receiver surface in 2.623 seconds at smoke settings (`1,000` samples/transmitter, depth `1`).
- Python checks, the API smoke suite, data validation, and the production web build pass locally.

The 5 m demonstration uses one million samples per transmitter and tile, depth four, LoS, reflection, refraction, and diffraction. It completed in 553 seconds wall time on two RTX 3070s. It is a representative-band demonstration rather than the final all-frequency acceptance run. WSL2/Docker and DGX container smoke tests remain host-dependent because neither platform is available on this development machine.

## Implemented system

- SHA-256 data provenance with retrieval dates, source URLs, licences, schemas, and processing metadata.
- Streaming ISED normalization without retaining public contact fields.
- NAD83 / UTM Zone 18N scene coordinates with local-origin geometry and terrain-relative antenna heights.
- Direct LOD1/HRDEM conversion to PLY and Mitsuba XML; Blender is not required.
- Six configurable frequency-dependent Sionna material profiles. Current public geometry distinguishes terrain and building envelope material; more detailed surface classification can reuse the remaining profiles.
- Free-space upper-bound sector selection, 1 km tiles with 50 m overlap, deterministic seeds, atomic resumable queues, and deterministic core ownership during stitching.
- Per-sector path gain, RSS, estimated RSRP, SINR, strongest-server association, provenance, and fallback flags.
- ISED-derived directional weighting for gain, azimuth, downtilt, horizontal/vertical beamwidth, and front-to-back attenuation.
- Cached coordinate queries with a clearly labelled low-confidence free-space fallback outside completed ray-traced cells.
- Measurement validation, identity/frequency/geometry station matching, spatial 60/20/20 splits, uncalibrated baselines, bounded staged dB correction, and held-out metrics.
- FastAPI, React/MapLibre/deck.gl partner UI, Sionna RT GUI launch configuration, local dual-GPU, DGX/MIG, and SLURM launchers.

## Environment

The current Windows checkout is `D:\Projects\Sionna_RT_OTTAWA`. If `.venv` was moved with it, follow the [environment repair instructions](docs/operations.md#repair-the-windows-environment-after-moving-the-checkout) before launching the CLI or GUI. Keep `paths.data_root: data` in the project YAMLs.

For source development with Python 3.11 or 3.12:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all,dev]"
```

All direct Python dependencies are exactly pinned in `pyproject.toml`; Sionna RT is pinned to 2.1.0. The web build uses its committed pnpm lockfile.

## Rebuild the public-data snapshot

```bash
ottawa-rt fetch-data --width-m 2000
ottawa-rt normalize-ised --width-m 2000
ottawa-rt build-scene --width-m 2000
ottawa-rt validate
```

Acquisition records are written to `data/raw/provenance.json`. Raw and generated data are gitignored because of their size; copy or mount the `data/` directory when moving the project to another host.

## Benchmark and production run

Prepare a fresh production queue:

```bash
ottawa-rt benchmark --seconds-per-tile-band 120 --scene-vram-gb 4.5 --measured-width-m 2000
ottawa-rt simulate --width-m 2000 --run-id baseline-2000m
```

`data/processed/benchmark.json` is currently marked as extrapolated. Replace its inputs with measured elapsed time and peak allocation from a representative production-settings job before increasing the scene width.

On the existing checkout, `baseline-2000m` is already prepared. Run one worker per local GPU, retry any corrected failures, and stitch only after the queue finishes:

```bash
CUDA_VISIBLE_DEVICES=0 ottawa-rt worker baseline-2000m &
CUDA_VISIBLE_DEVICES=1 ottawa-rt worker baseline-2000m &
wait
ottawa-rt retry-failed baseline-2000m
ottawa-rt stitch baseline-2000m
```

The queue uses atomic job claims, so interrupted workers can resume without duplicating completed tiles. Stitched outputs use non-overlapping 1 km cores; overlaps are retained for seam diagnostics.

## Query and browser application

```bash
ottawa-rt query --latitude 45.3404552 --longitude -75.9111420
ottawa-rt serve
```

Open <http://localhost:8000>. The browser provides 2D/3D scene geometry, stations, run/band coverage layers, coordinate and height queries, ranked service predictions, confidence/fallback warnings, measurement overlays, and measured-versus-simulated comparison after calibration.

Three visual modes are available:

- **Photo 3D** streams Google Photorealistic 3D Tiles through Cesium ion and overlays the saved Sionna RT coverage products. It is a presentation layer only; it is not used as propagation geometry.
- **Planning 3D** uses Cesium World Terrain plus the reproducible public Ottawa LOD1 building layer.
- **Planning map** keeps the MapLibre/deck.gl engineering view for inspecting stations and coverage without photorealistic detail.

Put `VITE_CESIUM_ION_TOKEN=...` in `web/.env.local`, rebuild the web app, and restart the API. Cesium Asset Depot content is streamed subject to its provider's attribution and usage terms; it is not copied into the simulation snapshot. The RF artifacts stay portable and independently reproducible from Ottawa/NRCan/ISED public data.

For UI development:

```bash
cd web
pnpm install --frozen-lockfile
pnpm run dev
```

## Container, WSL2, DGX, and SLURM

```bash
docker build -t ottawa-sionna-rt:latest .
docker compose --profile local up api
RUN_ID=baseline-2000m docker compose --profile local up worker-0 worker-1
```

On a DGX, copy the same repository and `data/` artifact tree, build the same image, then start one container per visible physical GPU or MIG device:

```bash
scripts/run-dgx-workers.sh baseline-2000m ottawa-sionna-rt:latest
```

Re-run the sizing benchmark on the DGX. A larger memory device may allow a larger common scene, while ray-tracing throughput can differ from an RTX GPU. Every job must still fit a single selected GPU/MIG partition. `scripts/slurm-worker.sh` submits the same worker and queue through a one-GPU SLURM allocation.

## Receiver measurements and calibration

Copy `examples/receiver-measurements.csv` and populate it with receiver-node readings. Column names are case-insensitive; `latitude`/`lat`, `longitude`/`lon`, `frequency_mhz`/`frequency`, and `physical_cell_id`/`pci` aliases are accepted.

Required fields are `timestamp`, position, frequency, and at least one of RSRP/RSSI. RSRP is required by the current fitting objective. Recommended identity fields are operator, cell ID, and PCI; height defaults to 1.5 m AGL.

```bash
ottawa-rt import-measurements receiver-readings.csv
ottawa-rt calibrate data/measurements/receiver-readings.jsonl --run-id baseline-2000m
```

Matching first uses identity when present, then frequency, spatial plausibility, and sector direction. Ambiguous matches remain flagged and are excluded from fitting. Spatial blocks—not individual random rows—are assigned 60/20/20 to train, validation, and test.

The first calibration stage fits a global receiver offset, effective frequency-group corrections, and regularized per-sector EIRP offsets capped at +/-6 dB. It reports MAE, RMSE, bias, P90 absolute error, and service-threshold accuracy for each split. Physical material/scattering optimization is not implemented in this calibration stage; the raw per-sector maps and stable measurement schema support that later fitting work without rebuilding the public geometry.

`calibrate` also rebuilds native-resolution per-band and combined coverage from the corrected per-sector powers, including strongest-server selection and SINR. The GUI's **Prediction model** selector compares **Measurement-calibrated** with **Original simulation**, and both map and coordinate queries use the selected model. Calibration versions invalidate raster/image caches; raw simulation artifacts are preserved. See [the full measurement workflow](docs/operations.md#14-recalculate-coverage-after-adding-receiver-measurements). Physical material/geometry optimization and re-tracing remain separate from these fitted power corrections.

## Network traffic experiments

NVIDIA Sionna SYS adds uplink/downlink scheduling, adaptive or fixed NR MCS, stochastic transport-block decoding, and finite packet traffic queues. The default remains downlink: 24 outdoor UEs with CIRs traced on the existing Ottawa terrain/building scene, with transmitter batching for the RTX 3070. Saved results appear in the GUI's **UE performance** panel. See [the network run guide](docs/network-simulation.md) and [uplink/modulation experiments](docs/uplink-modulation-experiments.md) for sensor transmission, MCS sweeps, power/interference and calibration limits.

```bash
python -m pip install -e ".[network]"
ottawa-rt simulate-network --config config/demo-4km-5m.yaml --scenario config/network-ottawa.yaml
```

The traffic model reports delivered throughput, average allocated bandwidth, packet loss/backlog and delivered-packet queue delay. Its E2E delay adds a configured core delay; this is an NR system-level abstraction rather than a complete network protocol stack.

For a separate AODT deployment, see the [AODT operator runbook](docs/aodt.md) and [UE/configuration and scheduling reference](docs/aodt-experiments.md). Those guides document the current 1.5.1 workflow and the conversion/calibration work needed for Ottawa; an AODT worker and Ottawa importer have not been validated in this checkout.

Sionna SYS allocates resource blocks in slots available to the selected UL/DL direction using proportional fair (PF) scheduling, with adaptive or fixed modulation/coding. PF uses achievable rate and throughput history. Video, interactive and sensor profiles currently differ in arrivals, demand and queue size; they do not assign application priority, guaranteed rates, 5QI policies or packet deadlines. AODT RAN mode also includes MAC resource scheduling, with documented PF/RR modes, but requires its separate supported worker deployment. See [scheduling behavior and metrics](docs/network-simulation.md#radio-resource-allocation-and-ue-priority).

| Metric | Interpretation |
| --- | --- |
| UE/network goodput | Delivered packet payload Mbps, mean and lower fifth percentile UE performance, and a time series. |
| Bandwidth | Configured carrier, occupied NR bandwidth, resource-block budget and average UE allocation. |
| Delay | Delivered-packet radio/queue mean, P50, P95 and P99; estimated E2E adds configured core delay. |
| Delivery and congestion | Delivered fraction, finite-buffer drops, pending packets and queue evolution. |
| Radio and scheduling | RSS, estimated RSRP, SINR, serving sector, MCS, predicted transport-block error rate and fairness. |
| Calibration evidence | Pinned fit ID/hash, held-out errors and frequency-group training support; no throughput or latency calibration from RSRP alone. |

### Scheduled mobile-node-calibrated campaign

`config/campaign-legget-20261001.yaml` queues **116 UE scenarios** with 12/24/60/120 UEs, two seeds and different load, bandwidth, receiver-noise and downlink-airtime conditions. It first runs 56 cases on the measured 6 km scene, then prepares a **10 km × 10 km square around 350 Legget Drive with a 1 m receiver grid**, fits a fresh model from the original mobile-node CSVs, and runs 60 cases on the new scene. The larger dataset is isolated under `data/campaigns/`; its RF queue has 5,600 tile/frequency jobs across 14 selected groups.

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli prepare-campaign
.\.venv\Scripts\python.exe -m ottawa_rt.cli run-campaign
.\.venv\Scripts\python.exe -m ottawa_rt.cli campaign-status
```

Do not launch a second supervisor if one is already running. The [campaign guide](docs/experiment-campaigns.md) covers background startup, the full matrix, dependencies, paths, recovery and GUI access. The 1 m setting describes receiver/coverage sampling; the existing terrain-mesh convention remains 10 m. The current 3.5 GHz fit applies mobile-node receiver corrections through extrapolation, while the 2120 MHz comparisons have group-level training support. All new scenarios require calibration; unsupported bands and held-out errors stay visible in results.

## API

- `GET /health`
- `GET /v1/project`
- `GET /v1/query?latitude=...&longitude=...&height_agl_m=...&calibrated=true`
- `GET /v1/stations`
- `GET /v1/runs`
- `GET /v1/provenance`
- `GET /v1/calibration`
- `GET /v1/measurements`
- `GET /v1/network` and `GET /v1/network/{name}`
- `GET /v1/network/{name}/ues` and `GET /v1/network/{name}/csv`
- `GET /v1/coverage/{run_id}/{tile.npz}`
- `GET /v1/scenes/{scene_name}/buildings`

OpenAPI documentation is available at `/docs`.

## Public data and licences

- ISED Spectrum Management System terrestrial site extract — Open Government Licence - Canada.
- NRCan HRDEM / CanElevation DTM — Open Government Licence - Canada.
- City of Ottawa 3D Buildings LOD1 — Open Government Licence - City of Ottawa.

The exact URLs, retrieval timestamps, file sizes, checksums, and processing metadata are in `data/raw/provenance.json`.

## Scientific limitations

- V1 is outdoor-only and uses LOD1 building envelopes, not detailed facade classes or indoor geometry.
- ISED records describe reported spectrum installations and may not exactly represent current commercial deployment. Every affected record exposes provenance and defaults.
- RSRP is estimated from simulated received power and configured bandwidth/resource-block assumptions.
- Exact vendor antenna patterns require manufacturer pattern files; the current model uses parameterized ISED pattern cuts.
- ITU-R P.2040 fits for concrete, metal, and ground begin at 1 GHz in Sionna 2.1. Sub-GHz runs retain their actual RF carrier but explicitly freeze unsupported constitutive parameters at the nearest documented fit boundary; each affected tile records this fallback in `material_frequency_fallbacks`.
- A query outside completed Sionna cells returns a low-confidence preview with an explicit warning.
- Predictions support RF planning and comparison; they do not guarantee coverage, scheduling capacity, or operator service.
