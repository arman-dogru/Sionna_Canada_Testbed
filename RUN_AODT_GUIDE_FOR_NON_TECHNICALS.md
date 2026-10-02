# AODT: Running and Monitoring Experiments

This is the Aerial Omniverse Digital Twin (AODT) counterpart to [the Sionna RT run guide](RUN_RT_GUIDE_FOR_NON_TECHNICALS.md). It explains how to run a first experiment, place UEs, monitor the worker, and prepare a repeatable study.

The examples follow **AODT 1.5.1**, checked on **October 2, 2026**. They describe NVIDIA's separate deployment. This checkout currently runs Ottawa experiments with Sionna RT + SYS; it does not contain an installed AODT worker or a validated Ottawa-to-AODT scene converter. The AODT commands below require the installation described in [the setup runbook](docs/aodt.md).

For individual settings and units, keep [the AODT configuration reference](docs/aodt-configuration.md) open alongside this guide.

## 1. Understand the three parts

| Part | What it does | Where it runs |
| --- | --- | --- |
| Client | Loads the experiment YAML and requests a simulation. | A separate computer, or the worker host. |
| Worker | Traces radio propagation and, in RAN mode, simulates radio transmission and scheduling. | A supported NVIDIA GPU host running Ubuntu 22.04 and Docker. |
| Viewer | Shows the scene, UEs, and saved telemetry. | A computer with a suitable browser and access to the result services. |

**UE** means user equipment: the simulated handset or radio terminal. **RU** means radio unit: a base-station radio and its antenna panel. A **DU** supplies the associated radio configuration.

The current Windows workstation's RTX 3070s are outside NVIDIA's documented worker GPU list. A Windows or WSL client can connect to a supported remote worker. Use [NVIDIA's prerequisites](https://docs.nvidia.com/aerial/aodt/prerequisites) and [our installation steps](docs/aodt.md#2-prepare-a-supported-worker) before starting.

## 2. Choose the experiment type

| Goal | Mode |
| --- | --- |
| Study signal strength, paths, blockage, materials, or channel evolution. | EM: electromagnetic propagation. |
| Study radio throughput, allocation, MCS, DL/UL, MIMO, or PF/RR scheduling. | RAN: propagation plus the radio PHY/MAC. |
| Study packet queues, application latency, video/sensor priorities, or TCP. | Requires an explicit traffic/protocol integration; radio telemetry alone is insufficient. |

Start with a small EM installation check, then a small RAN check. The initial commands are in [the setup runbook](docs/aodt.md#4-verify-assets-em-and-ran-before-importing-ottawa). The following walkthrough uses the provided Tokyo RAN scene so scene conversion is not a prerequisite for the first test.

## 3. Terminal layout

| Terminal | Purpose | Location |
| --- | --- | --- |
| Terminal 1 | Launch the client and save its logs. | Linux/WSL client. |
| Terminal 2 | Follow the simulation worker's logs. | SSH session on the worker. |
| Terminal 3 | Observe GPU memory and activity. | SSH session on the worker. |

The Bash examples below run inside Linux or WSL, not directly in PowerShell. On Windows, open a WSL terminal for Terminal 1. A native Windows client is also supported; its build/Python setup is described in [the client runbook](docs/aodt.md#3-connect-a-client-and-identify-the-endpoints), with a [PowerShell launch alternative below](#16-native-windows-launch-alternative).

Stopping a log or GPU monitor with `Ctrl+C` stops that monitor. Interrupting the client can leave work running on the server: inspect worker state before submitting another job.

## 4. One-time preparation

Complete these steps once with the person administering the worker:

1. Install compatible worker/client components and record their source commit and image versions.
2. Start the worker stack and verify its services.
3. Upload NVIDIA's demo assets and Tokyo map to the configured storage bucket.
4. Confirm the client can reach gRPC and that the worker can read its scene/assets.
5. Confirm the viewer can reach the catalog and storage through its own endpoints.

The [setup runbook](docs/aodt.md) gives the installation, asset upload, and endpoint commands. An NGC key is required for the worker images; the project's Cesium or other API credentials do not provide that access.

The example client directory below is `/srv/aodt_1.5.1/client`. Replace it with your actual AODT client checkout. Replace `WORKER_HOST` with its reachable hostname or IP address.

## 5. Terminal 1: open the client environment

```bash
cd /srv/aodt_1.5.1/client
source .venv/bin/activate
export AODT_CLIENT_DIR="$PWD"
export PYTHONPATH="$AODT_CLIENT_DIR/build:$AODT_CLIENT_DIR/build/config"
export AODT_SERVER="WORKER_HOST:50051"
python -c "import dt_client, _config; print('AODT client is ready')"
```

These paths assume the Linux/WSL build in the setup runbook. Keep this environment separate from the Ottawa repository's `.venv`.

## 6. Create a first experiment configuration

Choose an unused run ID. The example ID encodes the scene, UE count, seed, date, and attempt. Use a new ID for a new experiment or a resolved retry; check both local outputs and the result catalog for existing IDs.

```bash
export AODT_RUN="tokyo-ran-u4-s41-20261002-r1"
mkdir -p studies/tokyo
```

Run this block from the client directory. It copies the complete native sample and changes only its ID, seed, and radio observation length:

```bash
python - <<'PY'
import os
from pathlib import Path
import yaml

run = os.environ["AODT_RUN"]
output = Path("studies/tokyo") / f"{run}.yaml"
if output.exists():
    raise FileExistsError(output)
config = yaml.safe_load(Path("tests/assets/quickstart_batched_ran.yaml").read_text())
config["db"]["sim_id"] = run
scenario = config["sim"]["Scenario"]["update"][0]["attributes"]
scenario.update(sim_is_seeded=True, sim_seed=41, sim_batches=1,
                sim_slots_per_batch=100, sim_samples_per_slot=1)
output.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
print(output)
PY
```

Open the generated YAML and check its scene, four UEs, RU, carrier, and storage destinations. File-import mode reads those settings from the YAML; adding a scene or S3 command-line option does not rewrite it. See [the native sample](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/b1bf1ee19bfefcc46fa941f136582a5e38d197ff/client/tests/assets/quickstart_batched_ran.yaml).

At 30 kHz spacing, 100 slots represent **0.05 seconds of radio time**. This is an installation/operation check. It is not a throughput measurement over five seconds, and its wall-clock duration will be longer than its radio duration.

## 7. Terminal 1: launch and save the logs

```bash
mkdir -p results
set -o pipefail
mkdir "results/$AODT_RUN" &&
cd "results/$AODT_RUN" &&
python "$AODT_CLIENT_DIR/examples/example_full_sim.py" \
  --server_address "$AODT_SERVER" \
  --import_option file \
  --yaml_file "$AODT_CLIENT_DIR/studies/tokyo/$AODT_RUN.yaml" \
  2>&1 | tee client.log
```

An existing result directory stops this launch block; inspect it or choose a new run ID. Leave the client running. It reports the loaded RU/UE counts, starts the full simulation, and prints completed time steps. It also writes `dt_server.log` in this result directory. [NVIDIA's full-run example](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/b1bf1ee19bfefcc46fa941f136582a5e38d197ff/client/examples/example_full_sim.py).

## 8. Terminal 2: follow the worker

On the worker host, from its AODT checkout root:

```bash
cd /srv/aodt_1.5.1
docker compose -f worker/docker-compose.yml ps
docker compose -f worker/docker-compose.yml logs --follow --tail 50 worker
```

Check that the worker accepts the scenario and advances through the requested work. AODT does not use the Sionna `queue/pending`, `processing`, and `done` directories. Keep client logs, worker logs, and exported results as its run evidence.

## 9. Terminal 3: watch the worker GPU

```bash
nvidia-smi -l 2
```

Run this on the worker, not the Windows client. GPU use can vary during initialization, export, and processing; a single low-utilization snapshot does not establish a stall. Use it with the worker logs.

## 10. Verify completion and open the viewer

Check all of the following before accepting a run:

- The loaded scene, RU count, UE count, and time settings match the saved YAML.
- The client reports completion without an error, with the requested time steps/batches.
- The expected tables/files exist under the simulation ID, including telemetry for RAN analysis.
- The exported effective `ran_config` matches the intended scheduler and radio settings.
- Every configured UE is included in the analysis, including UEs with zero service.

There is no Sionna `finalize` command for AODT. Its worker exports results according to the native YAML. Use [result verification and metrics](docs/aodt-experiments.md#7-metrics-resource-allocation-and-priority) for the required interpretation.

To start the viewer, from a configured AODT checkout with a compatible Node.js installation:

```bash
cd /srv/aodt_1.5.1/viewer
npm install
npm run build
npm run start
```

Open `http://localhost:3000`. In **Settings**, enter catalog and S3/MinIO addresses reachable by the browser, then select the simulation and inspect the UEs and results. [Viewer instructions](https://docs.nvidia.com/aerial/aodt/viewer-installation).

## 11. Set up the UEs for a study

| Placement | Use it for | How to create it |
| --- | --- | --- |
| Fixed coordinates | Repeatable comparisons, cell-edge tests, measured locations. | Add explicit UEs with IDs and stationary waypoints. |
| Random/procedural | UE density and placement variation. | Define a spawn polygon, UE count, indoor percentage, speed range, and seed. |
| Manual route | A controlled walk or drive. | Give a UE several waypoints with speed and pauses. |
| GPX route | Reproduce a measured route. | Supply a worker-readable GPX file and validate time/location alignment. |

The [fixed, random, and moving UE recipes](docs/aodt-experiments.md#3-place-ues) include copyable Python builders. The [configuration reference](docs/aodt-configuration.md#ues-and-placement) explains the corresponding fields and units.

For an initial fixed-UE change, import a complete base with `SimConfig.from_yaml_file()`, remove inherited UEs, add the new UEs/waypoints, disable procedural placement, and give the output a new simulation ID. For random placement, start with a base without inherited routes/spawn zones and verify the actual generated UE count.

For paired conditions, reuse and verify the exact UE positions and IDs. A seed helps reproducibility, but position generation can change when other configuration inputs change.

## 12. Experiments to run next

| Experiment | Change | Main comparison |
| --- | --- | --- |
| Density | 12, 24, 60, 120 UEs in the same area. | Per-UE rate, aggregate rate, fairness, zero-service fraction. |
| Scheduler | PF versus RR with the same UEs. | Allocation and rate distribution. |
| Placement | Near-RU, cell-edge, blocked, or wider-area positions. | Link quality and service distribution. |
| Receiver condition | UE/gNB noise figure. | SINR, MCS, failure rate, DL/UL radio rate. |
| DL/UL balance | A supported TDD pattern. | DL and UL separately over the full observation. |
| Reliability | Supported HARQ or BLER-target choices. | Failures, retransmissions, radio rate. |
| Mobility | Static versus a sufficiently long walking/GPX route. | Channel and service variation along the route. |
| MIMO/beamforming | Supported four- versus 64-antenna RU arrangements. | Layers and rate under changed antenna assumptions. |
| Propagation convergence | Ray/path budget on a fixed EM scene. | Prediction stability and runtime. |

Use [the detailed experiment matrix](docs/aodt-experiments.md#5-experiments-to-start-with) for controlled designs. A practical exploratory study is four densities, two seeds, and four conditions: 32 runs. Validate one small case per condition before the sweep.

The documented 1.5.1 RAN supports **100 MHz / 273 PRBs / 30 kHz spacing**. Our Sionna 20/40 MHz sweep is not an equivalent AODT configuration. PF/RR chooses radio allocation; application priority, packet delay, or end-to-end latency require their own policies and evidence. [Release limits](https://docs.nvidia.com/aerial/aodt/limitations).

## 13. Schedule a sweep

Prepare every native YAML first, with unique IDs. Run one controller per worker. Use [the sequential run-list recipe](docs/aodt-experiments.md#6-schedule-a-repeatable-experiment-sweep) to submit a group and save separate logs.

Group jobs by effective worker configuration. Finish the PF group before changing the worker-wide scheduler to RR. A filename such as `rr-u24.yaml` does not select RR. Save the full worker JSON, reload it when needed, and check the exported `ran_config`.

The Ottawa `prepare-campaign` / `run-campaign` commands schedule Sionna jobs. They currently do not schedule AODT jobs. After an interrupted AODT run, inspect server and storage state before creating an explicit remaining-run list; the simple run-list recipe does not provide automatic crash recovery.

## 14. Use the Ottawa terrain and mobile-node data

Follow [Ottawa scene preparation](docs/aodt.md#5-prepare-an-ottawa-scene) before replacing Tokyo. Our Mitsuba XML/PLY scene is not a native AODT scene. Verify the converted geometry, coordinate/elevation conventions, sector positions/powers, antenna directions, and material assignments.

For calibrated Ottawa studies, reproduce the measured links in an AODT EM run and fit AODT settings using the original mobile-node data and preserved spatial splits. Validate held-out errors before the RAN sweep. Our Sionna calibration JSON is not an AODT calibration model. See [the calibration procedure](docs/aodt.md#7-calibrate-against-the-mobile-node-data).

The current 10 km Sionna campaign continues separately. Its 1 m receiver grid is a sampling choice, not an AODT import setting or proof of 1 m scene accuracy.

## 15. Troubleshooting and daily procedure

| Symptom | First check |
| --- | --- |
| Client import fails | AODT environment, compiled Python version, and `PYTHONPATH`. |
| Cannot connect | Worker health, gRPC address, published port, network route. |
| Missing map/assets | Bucket, scene prefix, and uploaded demo bundle. |
| Wrong UE count | Inherited demo/manual/procedural/GPX sources. |
| Changed scheduler has no effect | Effective worker JSON and exported `ran_config`. |
| Viewer has no data | Completed exports and browser-reachable catalog/storage addresses. |
| Interrupted client | Whether server work is still active and which outputs exist. |

The day-to-day sequence is: choose an unused ID, generate/review the YAML, preserve the effective worker settings, launch one client, monitor the worker, verify exports, and record metrics with their definitions. Keep the scene/config hashes, calibration evidence, logs, and result references together. Use [the configuration rerun table](docs/aodt-configuration.md#what-must-be-repeated-after-a-change) when altering a study.

## 16. Native Windows launch alternative

After completing NVIDIA's MSVC/vcpkg client build and generating a reviewed YAML, use this PowerShell alternative to Section 7. Replace the example paths with your actual client and vcpkg locations. Use the Python interpreter used to build the bindings.

```powershell
$AodtClient = 'D:\AODT\aodt_1.5.1\client'
$AodtVcpkg = 'C:\src\vcpkg'
$AodtPython = Join-Path $AodtVcpkg 'installed/x64-windows/tools/python3/python.exe'
$AodtRun = 'tokyo-ran-u4-s41-20261002-r1'
$AodtYaml = Join-Path $AodtClient "studies/tokyo/$AodtRun.yaml"
$env:PYTHONPATH = "$AodtClient\build\Release;$AodtClient\build\config\Release"
$env:PATH = "$AodtVcpkg\installed\x64-windows\bin;$env:PATH"
$AodtResults = Join-Path $AodtClient "results/$AodtRun"
New-Item -ItemType Directory -Path $AodtResults -ErrorAction Stop | Out-Null
Push-Location -LiteralPath $AodtResults
try {
    & $AodtPython (Join-Path $AodtClient 'examples/example_full_sim.py') `
      --server_address 'WORKER_HOST:50051' --import_option file --yaml_file $AodtYaml `
      2>&1 | Tee-Object -FilePath client.log
    if ($LASTEXITCODE -ne 0) { throw "AODT client failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
```

This uses the same native YAML, remote worker, export verification, and monitoring procedure. It does not use the Ottawa Sionna virtual environment. [NVIDIA Windows build instructions](https://github.com/NVIDIA/aerial-omniverse-digital-twin/blob/b1bf1ee19bfefcc46fa941f136582a5e38d197ff/client/README.md#windows-msvc).
