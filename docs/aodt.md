# AODT setup and experiment runbook

Checked against NVIDIA AODT **1.5.1** documentation and the public client/worker source on **2026-10-02**. This guide covers a separate AODT deployment. The working Ottawa experiments use Sionna RT + SYS; an AODT worker, an Ottawa scene converter, and an `ottawa-rt` AODT backend have **not** been installed or validated here.

Use [AODT experiment configuration](aodt-experiments.md) for UE examples, experiment designs, scheduling, and interpreting results. Use [the Sionna runbook](operations.md) and [our calibrated campaign guide](experiment-campaigns.md) for the existing implementation.

For a first experiment, use [the beginner AODT run guide](../RUN_AODT_GUIDE_FOR_NON_TECHNICALS.md). For field meanings, units, and rerun requirements, use [the AODT configuration reference](aodt-configuration.md).

## 1. Choose the experiment backend

| Question | Use |
| --- | --- |
| Run our existing Ottawa scenes, packet loads, and calibrated density sweep now? | The implemented Sionna RT + SYS workflow in [network-simulation.md](network-simulation.md). |
| Study radio scheduling, MIMO, beamforming, link adaptation, or DL/UL PHY behavior with a detailed RAN? | AODT RAN on a supported worker, after scene preparation and calibration. |
| Study propagation, paths, antenna/material effects, or measurement fitting? | AODT EM mode; start here before adding RAN. |
| Measure application latency, TCP behavior, or enforce service priorities? | Define a traffic/protocol integration and validate it separately; see [metrics and priority](aodt-experiments.md#7-metrics-resource-allocation-and-priority). |

AODT combines a Python/C++ client, a containerized simulation worker, and a browser viewer. Its public project targets 5G/6G research; the available implementation and its release constraints determine which experiments are supported. [NVIDIA project](https://github.com/NVIDIA/aerial-omniverse-digital-twin), [RAN quickstart](https://docs.nvidia.com/aerial/aodt/ran-simulations).

The documented RAN configuration uses **100 MHz, 273 PRBs, and 30 kHz subcarrier spacing**, with four UE antennas and supported four- or 64-antenna RU arrangements. Our current 20/40 MHz SYS sweep cannot be transferred unchanged. [Release limitations](https://docs.nvidia.com/aerial/aodt/limitations).

## 2. Prepare a supported worker

Use a separate Ubuntu 22.04 host with a documented supported GPU: RTX 6000 Blackwell, GB10, RTX 6000 Ada, L40, or L40S. Install the release-compatible NVIDIA driver, Docker Compose v2, NVIDIA Container Toolkit, and Mike Farah's `yq` v4. Follow NVIDIA's host instructions for the precise driver/platform requirements. The local Windows workstation's two 8 GB RTX 3070s are outside this worker list. [Prerequisites](https://docs.nvidia.com/aerial/aodt/prerequisites), [worker installation](https://docs.nvidia.com/aerial/aodt/worker-installation).

On the worker, check the tools before starting:

```bash
nvidia-smi
docker compose version
yq --version
```

Obtain an NGC API key with access to the required worker images. Other credentials in this project's `.env`, including Cesium keys, do not grant NGC access. Load the NGC key into the worker environment using your secret-management mechanism, then authenticate without embedding it in command text:

```bash
printf '%s' "$NGC_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin
git clone https://github.com/NVIDIA/aerial-omniverse-digital-twin.git aodt_1.5.1
cd aodt_1.5.1
./worker/up.sh
docker compose -f worker/docker-compose.yml ps
docker compose -f worker/docker-compose.yml logs --tail 100 worker
```

These commands follow the current [worker deployment](https://docs.nvidia.com/aerial/aodt/worker-installation). The directory name does **not** pin a version. Select a client checkout compatible with the worker release, record `git rev-parse HEAD`, and archive the image digest and release manifest before scientific runs. Do not update a checkout mid-campaign.

Stop the stack when appropriate with:

```bash
docker compose -f worker/docker-compose.yml down
```

Keep its persistent storage when stopping; it contains the simulation evidence.

## 3. Connect a client and identify the endpoints

Keep the AODT checkout and Python environment separate from `D:\Projects\Sionna_RT_OTTAWA\.venv`.

Linux or WSL client, from the AODT checkout's `client/` directory:

```bash
sudo apt-get update
sudo apt-get install -y cmake protobuf-compiler-grpc libgrpc++-dev pkg-config python3-dev python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install pybind11 pyyaml omegaconf pytest numpy 'pyiceberg[pyarrow,s3fs]' duckdb pandas
cmake -B build -DCMAKE_BUILD_TYPE=Release -DPython3_EXECUTABLE=$(which python3) .
cmake --build build -j$(nproc)
export PYTHONPATH="$PWD/build:$PWD/build/config"
python -c "import dt_client, _config; print('AODT bindings import successfully')"
```

A remote client can operate without a GPU. Native Windows uses Visual Studio 2022/CMake/vcpkg and REMOTE transport. Follow the [client build instructions](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/README.md); use vcpkg's Python for the compiled bindings, rather than this project's existing Python environment. Native Windows does not support LOCAL_IPC.

Record the actual deployed addresses:

| Endpoint | Current Compose default | Used by |
| --- | --- | --- |
| Worker gRPC | `WORKER_HOST:50051` | Client control and remote simulation access. |
| MinIO S3 API | `http://WORKER_HOST:9000` | Asset upload, results, viewer access. |
| MinIO console | `http://WORKER_HOST:9001` | Storage inspection. |
| Nessie/Iceberg REST | `http://WORKER_HOST:19120/iceberg` | Exported result catalog. |

Resolve ports from `docker compose ... ps` and the deployed configuration. Some installation examples use S3 port **9002**, while current Compose defaults to **9000** through `MINIO_API_PORT`. Worker-internal URLs such as `http://minio:9000` and `http://nessie:19120/iceberg` are different from addresses reachable by a remote client/browser. [Compose source](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/worker/docker-compose.yml), [installation verification](https://docs.nvidia.com/aerial/aodt/verify-installation).

Use private-network access or tunnels with consistent endpoint mapping. Supply the configured storage credentials locally and keep secret-bearing YAMLs out of Git. The repository's demo MinIO credentials are suitable only for an isolated demo.

## 4. Verify assets, EM, and RAN before importing Ottawa

Download and unpack NVIDIA's asset bundle and Tokyo demo map using the links on [Verify Installation](https://docs.nvidia.com/aerial/aodt/verify-installation). From the worker checkout root, replace the input directories with the actual unpacked bundle roots:

```bash
./worker/copy_to_s3.sh /path/to/unpacked/assets test_data/assets
./worker/copy_to_s3.sh /path/to/unpacked/tokyo test_data/maps/tokyo
```

Confirm those keys exist in the configured `aerial-data` bucket. From `client/`, with its environment active, run the provided native examples:

```bash
python examples/example_full_sim.py \
  --server_address WORKER_HOST:50051 \
  --import_option file \
  --yaml_file tests/assets/quickstart_batched_em.yaml

python examples/example_full_sim.py \
  --server_address WORKER_HOST:50051 \
  --import_option file \
  --yaml_file tests/assets/quickstart_batched_ran.yaml
```

Follow the [EM quickstart](https://docs.nvidia.com/aerial/aodt/em-simulations) and [RAN quickstart](https://docs.nvidia.com/aerial/aodt/ran-simulations). Confirm the loaded RU/UE counts, completed time steps, and exported results. Preserve stdout and `dt_server.log` separately for each run. The included small numerical budgets are installation checks; benchmark and test convergence before treating them as a production model.

**File-import mode reads the YAML as supplied.** The example's S3, scene, and Iceberg command-line options apply to generated-string mode; they do not rewrite a file's endpoints or geometry. Edit the complete native YAML's storage settings for the worker's network and keep the client/viewer settings reachable from their respective hosts. [Full-simulation example source](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/examples/example_full_sim.py).

## 5. Prepare an Ottawa scene

### Connectivity pilot

A small OSM map can verify GIS/storage connectivity before developing an Ottawa importer. From `client/`:

```bash
python examples/example_prepare_map.py \
  --server_address WORKER_HOST:50051 \
  --s3_endpoint http://WORKER_HOST:9000 \
  --min_lon -75.924 --min_lat 45.331 \
  --max_lon -75.899 --max_lat 45.350 \
  --output_folder_key ottawa/legget-pilot-v1
```

Replace the host and configure the example's storage credentials locally. Inspect the resulting `/sim` and `/viz` assets. The current example explicitly sets `include_elevation=False`: this command is a **flat-terrain connectivity pilot**, not our NRCan-terrain model. [Scene-building workflow](https://docs.nvidia.com/aerial/aodt/scene-building), [map example source](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/examples/example_prepare_map.py).

### Scientific Ottawa model

The documented GIS imports use OSM or CityGML. Our Mitsuba XML/PLY scenes and Sionna coverage arrays are not a documented drop-in AODT import. Build and validate a conversion pipeline before claiming the same environment was tested. [Scene-generation interfaces](https://docs.nvidia.com/aerial/aodt/scene-generation).

For a matched comparison, preserve:

1. The same immutable Ottawa building and NRCan terrain snapshots, geographic extent, and surrounding context.
2. Coordinate transforms from the project's NAD83 / UTM 18N data to AODT's georeferenced scene; verify several building corners, the anchor, and elevation points numerically.
3. Terrain shape, material assignments, and each ISED sector's frequency, power interpretation, position, height, azimuth, and tilt. Preserve existing fallback flags.
4. The mobile-node measurement snapshot, cell/sector matching decisions, and spatial train/validation/test membership.
5. An export manifest with input hashes, converter version, scene units, vertical datum, and a sample overlay reviewed in the viewer.

The anchor is `45.340455200216, -75.91114196736`. Prefer latitude/longitude for initial placements; Sionna's local `(0, 0)` cannot be assumed to equal AODT's scene origin. The 1 m receiver spacing in our large campaign is separate from its 10 m terrain mesh.

The documented GIS OSM request limit is **25 km²**, while our 10 km × 10 km campaign covers **100 km²**. Plan smaller validated windows or a separately validated import route. Tiling requires surrounding geometry and interfering sectors; independently simulated windows cannot simply be summed into a whole-network throughput result. [GIS constraints](https://docs.nvidia.com/aerial/aodt/scene-generation).

The existing 116-case supervisor remains a Sionna campaign. It does not dispatch AODT jobs or transfer calibration models.

## 6. Create and run a native scenario

Start with the matching release's complete [RAN sample YAML](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/tests/assets/quickstart_batched_ran.yaml) and [builder example](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/client/examples/example_client_yaml_config.py). For Ottawa, replace the scene and Tokyo deployment with validated Ottawa assets and RU/DU/panel definitions; save a reviewed `studies/legget/base-ran.yaml` in the AODT client checkout.

Generate the desired fixed/procedural/GPX UEs as shown in [the experiment reference](aodt-experiments.md#3-place-ues). Give each variant a new simulation ID and output YAML. Then run:

```bash
python examples/example_full_sim.py \
  --server_address WORKER_HOST:50051 \
  --import_option file \
  --yaml_file studies/legget/legget-manual-u2-s41.yaml
```

Before a long run, check a short case for the intended scene, exact UE count, antenna arrangement, frequency, and export destinations. Then increase the radio duration and compare repeat seeds. Use [the scheduling recipe](aodt-experiments.md#6-schedule-a-repeatable-experiment-sweep) to serialize a sweep on one worker.

## 7. Calibrate against the mobile-node data

Retain the pinned measurement snapshot and spatial splits from [our campaign](experiment-campaigns.md#mobile-node-calibration-and-evidence). Refit the AODT model on the training split and select settings using validation. Reserve the test split for the final report. Our Sionna `campaign-model.json` describes fitted power corrections; it is not an AODT calibration file.

AODT calibration first needs a base **EM** run with full ray paths and exported storage, then a matching calibration scenario and `client.run_calibration()`. Configure measured RU/UE links, targets, time indices, and an output prefix through the [calibration workflow](https://docs.nvidia.com/aerial/aodt/calibration). Verify the release's measurement-file schema before converting our CSVs; matching and temporal alignment are part of that conversion.

NVIDIA documents calibration-specific constraints including a 1×1 RU panel, accurate RU height, and only one target UE. Use a separate calibration configuration, then validate the fitted settings in the intended RAN antenna configuration. For GPX calibration, disable pathfinding so samples retain their measured locations. [Calibration limitations](https://docs.nvidia.com/aerial/aodt/limitations).

The current Compose mount maps `worker/data` to `/data`, so a host file such as `worker/data/measurements/link.csv` can be referenced as `/data/measurements/link.csv` by the worker. A Windows client path is not automatically a worker-readable path. [Worker mounts](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/main/worker/docker-compose.yml).

Report held-out MAE, RMSE, bias, P90 error, sample counts, excluded/ambiguous matches, and frequency support for both models. Our existing mobile-node data primarily calibrate received power; they do not calibrate traffic arrival processes or latency. Mark unsupported frequencies, including weakly supported 3.5 GHz predictions, as extrapolation. Fit complex material/beam parameters only when the measurements constrain them.

## 8. Inspect results and troubleshoot

Build the browser viewer from the AODT checkout root:

```bash
cd viewer
npm install
npm run build
npm run start
```

Open `http://localhost:3000`. Use the release's required Node.js version and a WebGL2 browser; set viewer storage/catalog addresses that the browser can reach. Select the simulation and inspect geometry, UEs, channels, and telemetry. [Viewer installation](https://docs.nvidia.com/aerial/aodt/viewer-installation).

Our Ottawa GUI does not automatically load these results. Keep AODT catalog references, Parquet exports, logs, and manifests together; see [metrics and artifact verification](aodt-experiments.md#7-metrics-resource-allocation-and-priority).

| Symptom | Check |
| --- | --- |
| `dt_client` or `_config` import fails | Activate the AODT environment; use the build's Python ABI and correct absolute `PYTHONPATH`. |
| gRPC connection fails | Worker container health, actual published port, address, and tunnel/firewall mapping. |
| Assets are missing | Bucket, bundle root, scene key, and worker-readable asset paths. |
| S3/catalog errors or empty viewer | Actual MinIO port; internal versus external DNS; credentials; exported tables and catalog registration. |
| Wrong UE count | Remove inherited demo UEs; check procedural/manual/GPX sources and the loaded worker status. |
| Bandwidth/panel rejection | Check the release's RAN limits and DU/RU/panel consistency. |
| Calibration cannot find inputs | Base full-ray-path EM export, identical scene/link IDs, supported CSV schema, and container mount paths. |
| GPU memory exhausted | Benchmark a new smaller scene/UE case; give numerical changes new IDs and document them. |

Use the current worker/client documentation together. Archived 1.4 workflows using the old Omniverse application should be followed only with that archived deployment.
