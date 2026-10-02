# Simulation and GUI runbook

This is the operator guide for running the Ottawa Sionna RT pipeline from the repository root. It covers the public-data build, ray-tracing jobs, result finalization, the browser GUI, and GPU deployment. See [configuration.md](configuration.md) before changing geographic or RF parameters.

## 1. Choose one configuration and one new run ID

Every command in a run must use the same configuration file. The examples below use the completed high-resolution profile:

```text
config/demo-4km-5m.yaml
```

Use a unique run ID that describes the extent, resolution, purpose, and date, for example:

```text
ottawa-4km-5m-multiband-20260919
```

Do not reuse a run ID after changing the configuration, width, frequency list, scene, or simulation settings. Queue creation preserves already-completed jobs, so reusing an ID can produce a scientifically invalid mixture of old and new tile outputs.

## 2. Prerequisites

For a source installation:

- Python 3.11 or 3.12.
- An NVIDIA driver visible through `nvidia-smi` for ray tracing.
- Node.js and pnpm for rebuilding or developing the browser GUI (the D: checkout was checked with pnpm 12.4.2).
- Enough disk space for `data/raw`, scene meshes, tile outputs, and stitched arrays.

Windows PowerShell setup for a new environment (use the repair instructions below for an existing moved `.venv`):

```powershell
Set-Location -LiteralPath 'D:\Projects\Sionna_RT_OTTAWA'
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[all,dev]"
pnpm --dir web install --frozen-lockfile
```

Ubuntu, WSL2, or DGX host setup:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all,dev]"
pnpm --dir web install --frozen-lockfile
```

Confirm that the GPUs and CLI are available:

```bash
nvidia-smi
ottawa-rt --help
```

If the console script is not on `PATH`, every example can be run as `python -m ottawa_rt.cli ...` instead of `ottawa-rt ...`.

### Repair the Windows environment after moving the checkout

The current Windows checkout is `D:\Projects\Sionna_RT_OTTAWA`. A copied virtual environment can retain the previous checkout path in activation scripts, executable launchers, and its editable package registration. Complete this repair before using a moved `.venv`; close simulation and GUI Python processes using that environment first.

For this checkout's existing Python 3.12 environment, refresh activation scripts and re-register the local project while keeping its installed dependencies:

```powershell
Set-Location -LiteralPath 'D:\Projects\Sionna_RT_OTTAWA'
py -3.12 -m venv .venv --without-pip
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

python -c "import sys, ottawa_rt; print(sys.executable); print(ottawa_rt.__file__)"
python -c "from ottawa_rt.config import load_settings; s = load_settings('config/demo-6km-2p5m.yaml'); print(s.paths.root); print(s.paths.data)"
python -m ottawa_rt.cli --help
python -m ottawa_rt.cli validate --config config/demo-6km-2p5m.yaml
```

The printed interpreter and package paths must be under `D:\Projects\Sionna_RT_OTTAWA`; the data path must be `D:\Projects\Sionna_RT_OTTAWA\data`. The editable install uses pip's isolated build environment and may download build tools, while `--no-deps` preserves the installed application dependencies. Use Python 3.11 instead of 3.12 only for an existing 3.11 environment.

Use `python -m pip`, `python -m pytest`, and `python -m ottawa_rt.cli` for commands in the moved environment: third-party executable launchers can still retain their original location. This repair refreshes the project's `ottawa-rt` launcher. If the copied environment cannot run, create a fresh environment before installing the dependencies.

Keep the project YAMLs under `config\` with `paths.data_root: data`. This resolves the data tree relative to the checkout, so no absolute drive path is needed in the YAML. Scene, queue, and run references are also stored relative to the project. Moving the checkout alone does not require a new run ID or rebuilding existing RF results; keep the entire `data\` tree with the project.

If using WSL, enter this Windows checkout with `cd /mnt/d/Projects/Sionna_RT_OTTAWA`. Use a separate Linux environment, for example `.venv-wsl`, rather than the Windows `.venv`:

```bash
cd /mnt/d/Projects/Sionna_RT_OTTAWA
python3.11 -m venv .venv-wsl
source .venv-wsl/bin/activate
python -m pip install -e ".[all,dev]"
```

Docker's `./data:/app/data` mount follows the checkout when launched from its root. The DGX/SLURM launchers likewise resolve `data/` from their working directory.

### Reinstall the GUI dependencies on the D: drive

The current D: volume uses exFAT. The web workspace is configured with `nodeLinker: hoisted` and `packageImportMethod: copy` so installation uses a flat directory of copied packages. These settings are documented in the [pnpm node-modules reference](https://pnpm.io/settings/node-modules).

Reinstall the locked dependencies once after moving the checkout or changing the dependency layout, then rebuild:

```powershell
Set-Location -LiteralPath 'D:\Projects\Sionna_RT_OTTAWA'
pnpm --dir web install --frozen-lockfile --force
pnpm --dir web build
```

Keep `web/pnpm-workspace.yaml` with the checkout. If PowerShell blocks `pnpm.ps1`, use `pnpm.cmd` instead. Preserve the existing `web/.env.local` when moving the project so the GUI retains its Cesium settings.

## 3. Start the GUI with the existing completed run

The repository already has a completed 4 km by 4 km, 5 m multiband run when the `data/` artifacts are present.

Production-style local launch:

```powershell
pnpm --dir web build
python -m ottawa_rt.cli serve --config config/demo-4km-5m.yaml --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The API documentation is at <http://127.0.0.1:8000/docs> and health status is at <http://127.0.0.1:8000/health>.

The browser provides:

- Planning Map for engineering inspection.
- Planning 3D using reproducible public Ottawa geometry.
- Photo 3D using Cesium ion and Google Photorealistic 3D Tiles.
- Run, band, and metric selection for path gain, RSS, RSRP, and SINR.
- A `Combined · best available band` layer for finalized multiband runs.
- Station markers, coordinate queries, receiver height, ranked sectors, confidence flags, and calibration comparisons.

Selecting a run recenters the map on that run's bounds. Photo 3D is only a visualization layer: Sionna traces against the saved scene under `data/scenes`, not against streamed Cesium/Google geometry.

The Combined layer takes the best finite value across the simulated bands at each receiver cell. It is useful as a presentation overview, but it is not carrier aggregation, one operator's guaranteed service footprint, or a capacity estimate. For demonstrations, lead with Combined RSRP, then compare 890 MHz RSRP for macro coverage, 2655 MHz RSRP/SINR for a capacity layer, and optionally 3505 MHz SINR for localized 5G behavior.

## 4. Configure Cesium ion

The Cesium token is compiled into the browser bundle. It is not read by the Python process at runtime.

```powershell
Copy-Item web/.env.example web/.env.local
```

Edit `web/.env.local`:

```dotenv
VITE_CESIUM_ION_TOKEN=your_token_here
VITE_CESIUM_ENABLE_OSM_BUILDINGS=true
VITE_CESIUM_ENABLE_PHOTOREALISTIC=true
VITE_CESIUM_TILE_CACHE_MB=1024
VITE_CESIUM_TILE_CACHE_OVERFLOW_MB=512
```

Then rebuild and restart the API:

```powershell
pnpm --dir web build
python -m ottawa_rt.cli serve --config config/demo-4km-5m.yaml --host 127.0.0.1 --port 8000
```

`web/.env.local` is gitignored. Do not place a token in a ZIP, commit, YAML configuration, screenshot, or run artifact. If a token is exposed, revoke it in Cesium ion and issue another one.

### Demo preparation and 10 km viewing

The coverage rasters are generated once under `data/runs/<run-id>/cache/coverage` and served with immutable browser-cache headers. Open every band/metric needed for the demo once before presenting so those local overlays are warm.

Google Photorealistic 3D Tiles are streamed and refined for the current camera view. The GUI keeps up to `VITE_CESIUM_TILE_CACHE_MB` in GPU memory, plus the configured overflow while a view is refining, and preloads the destination of camera flights. A 1 GB cache with 512 MB overflow is appropriate for the 8 GB RTX 3070 demo machine; reduce it to 512/256 if the browser reports GPU-memory pressure.

For a smooth live demo:

1. Use a wired connection and close other GPU-heavy applications.
2. Open Photo 3D five to ten minutes before presenting.
3. Visit the overview and the exact close-up viewpoints in presentation order, then leave the tab open.
4. Wait for the status to say `Google Photo 3D ready` before beginning.
5. Move between rehearsed views with camera flights rather than rapidly spinning across the whole city.

Do not bulk-download or rehost Google's 10 km photorealistic area. Its tiles may only use the normal HTTP caching allowed by the service response. If a guaranteed offline 10 km demo is required, convert Ottawa's licensed public terrain/building/imagery sources into a self-hosted 3D Tiles tileset and select that instead of Google Photo 3D. The Sionna coverage cache can remain unchanged.

## 5. Build a fresh public-data snapshot and scene

The configured width is the output region. Scene acquisition and geometry also include `area.context_m` on every side.

```powershell
python -m ottawa_rt.cli fetch-data --config config/demo-4km-5m.yaml --width-m 4000
python -m ottawa_rt.cli normalize-ised --config config/demo-4km-5m.yaml --width-m 4000
python -m ottawa_rt.cli build-scene --config config/demo-4km-5m.yaml --width-m 4000
python -m ottawa_rt.cli validate --config config/demo-4km-5m.yaml
```

Use `--force` with `fetch-data` only when intentionally refreshing the public snapshots. Use `--force` with `build-scene` after changing the anchor, context, source geometry, terrain, or material definitions.

Key outputs are:

| Artifact | Purpose |
| --- | --- |
| `data/raw/provenance.json` | Source URLs, retrieval dates, licences, sizes, and SHA-256 checksums. |
| `data/processed/sectors.jsonl` | Normalized ISED sector-frequency records and fallback flags. |
| `data/processed/sectors.summary.json` | Normalization summary. |
| `data/scenes/legget-4000m/scene.xml` | Mitsuba/Sionna scene entry point. |
| `data/scenes/legget-4000m/scene_metadata.json` | Bounds, local origin, CRS, materials, and geometry statistics. |
| `data/scenes/legget-4000m/meshes/` | Terrain and building PLY meshes. |

## 6. Benchmark before increasing the area

Measure one representative tile on one GPU and record elapsed time and peak GPU memory. Then feed those measurements to the sizing estimator:

```powershell
python -m ottawa_rt.cli benchmark `
  --config config/demo-4km-5m.yaml `
  --seconds-per-tile-band 120 `
  --scene-vram-gb 4.5 `
  --measured-width-m 4000 `
  --band-count 9
```

The estimator writes `data/processed/benchmark.json`. `max_vram_fraction` is 0.90 by default; every job must fit one GPU or one MIG device. VRAM is never pooled across GPUs.

For a low-cost smoke test, use a separate run ID and a single frequency. Never let a smoke job write into the production run queue:

```powershell
python -m ottawa_rt.cli simulate `
  --config config/demo-4km-5m.yaml `
  --width-m 4000 `
  --run-id smoke-4km-1960 `
  --frequency-mhz 1960

python -m ottawa_rt.cli worker smoke-4km-1960 `
  --config config/demo-4km-5m.yaml `
  --once `
  --samples-per-tx 1000 `
  --max-depth 1
```

## 7. Run a local dual-GPU simulation

Set `compute.local_gpu_count: 2` in the selected YAML. `simulate --execute` prepares the queue, launches one worker per configured GPU, sets `CUDA_VISIBLE_DEVICES` for each process, and waits for all workers to exit.

All discovered eligible frequency groups:

```powershell
python -m ottawa_rt.cli simulate `
  --config config/demo-4km-5m.yaml `
  --width-m 4000 `
  --run-id ottawa-4km-5m-multiband-20260919 `
  --execute
```

Selected 5 MHz frequency buckets only:

```powershell
python -m ottawa_rt.cli simulate `
  --config config/demo-4km-5m.yaml `
  --width-m 4000 `
  --run-id ottawa-4km-5m-selected-20260919 `
  --frequency-mhz 1960 `
  --frequency-mhz 2150 `
  --frequency-mhz 3505 `
  --execute
```

Each `--frequency-mhz` value is mapped to the implemented 5 MHz bucket. The command reports an error and lists available buckets when a requested group is absent.

For Linux or WSL, the same command uses backslash line continuations:

```bash
ottawa-rt simulate \
  --config config/demo-4km-5m.yaml \
  --width-m 4000 \
  --run-id ottawa-4km-5m-multiband-20260919 \
  --execute
```

Job count is:

```text
ceil(width_m / tile_size_m)^2 × number_of_frequency_groups
```

For the 4 km/5 m profile, 500 m tiles produce 64 tiles. Nine groups therefore produce 576 independent jobs. Output grid width is `width_m / cell_size_m`, or 800 by 800 cells per band in this profile.

## 8. Monitor, resume, retry, and finalize

Queue state is represented by JSON files under:

```text
data/runs/<run-id>/queue/{pending,processing,done,failed}
```

PowerShell status check:

```powershell
$run = 'ottawa-4km-5m-multiband-20260919'
Get-ChildItem "data/runs/$run/queue" -Directory | ForEach-Object {
  [pscustomobject]@{State = $_.Name; Jobs = (Get-ChildItem $_.FullName -File).Count}
}
```

Workers use atomic claims. Starting workers again resumes pending jobs and leaves completed jobs intact. To resume manually, use one terminal per GPU:

```powershell
$env:CUDA_VISIBLE_DEVICES = '0'
python -m ottawa_rt.cli worker ottawa-4km-5m-multiband-20260919 --config config/demo-4km-5m.yaml
```

In the second terminal, use GPU `1`. After correcting the cause of failed jobs:

```powershell
python -m ottawa_rt.cli retry-failed ottawa-4km-5m-multiband-20260919 --config config/demo-4km-5m.yaml
```

An abrupt process or machine kill can leave its claimed JSON in `queue/processing`. Before recovering such a record, verify that the `claimed_by` host and PID are no longer running. Move only that stale JSON back to `queue/pending`; never move a record owned by a live worker.

Once `pending`, `processing`, and `failed` are all zero, stitch and render:

```powershell
python -m ottawa_rt.cli finalize ottawa-4km-5m-multiband-20260919 --config config/demo-4km-5m.yaml
```

If finalization runs in a separate terminal while workers are active:

```powershell
python -m ottawa_rt.cli finalize ottawa-4km-5m-multiband-20260919 `
  --config config/demo-4km-5m.yaml `
  --wait `
  --poll-seconds 30
```

`--wait` waits for pending and processing jobs, but it does not automatically retry failed jobs.

## 9. Understand the saved run artifacts

Every GUI-visible run is self-describing under `data/runs/<run-id>`:

| Path | Contents |
| --- | --- |
| `run.json` | Run ID, model version, scene, bounds, grid size, bands, sector IDs, ray settings, and queue status. |
| `surfaces/` | Terrain-draped receiver surfaces for each tile. |
| `queue/` | Durable atomic job records, worker metrics, and errors. |
| `tiles/*.npz` | Raw per-tile/per-band metrics and per-sector information used by queries and calibration. |
| `stitched/<frequency>MHz.npz` | Full-area path gain, RSS, RSRP, SINR, and compact serving-sector index arrays with a sector-ID lookup table. |
| `stitched/all-bands.npz` | Aggregate strongest coverage across bands, including the winning frequency and compact serving-sector index. |
| `stitched/stitch-report.json` | Completed tile counts and overlap seam diagnostics. |
| `cache/coverage/*.png` | Versioned browser-ready coverage rasters generated on first view and reused by both 2D and 3D GUIs. |
| `visualization/*-summary.png` | Reference-style three-panel engineering figures. |
| `visualization/visualization-report.json` | Image sources, WGS84 bounds, grid size, and panel names. |
| `finalization.json` | Final queue, stitching, visualization, and completion status. |

Keep `run.json`, raw tiles, stitched products, visualization reports, and the corresponding scene/data provenance together when moving a run to another machine. The browser coverage layer uses the saved WGS84 bounds and grid metadata; it does not infer them from the display map.

## 10. Query and render from the command line

```powershell
python -m ottawa_rt.cli query `
  --config config/demo-4km-5m.yaml `
  --latitude 45.3404552 `
  --longitude -75.9111420 `
  --height-agl-m 1.5 `
  --run-id demo-4km-5m-multiband-20260918 `
  --frequency-mhz 1960
```

Recreate reference figures without retracing:

```powershell
python -m ottawa_rt.cli render-run demo-4km-5m-multiband-20260918 `
  --config config/demo-4km-5m.yaml
```

Add `--frequency-mhz 1960` to render one stitched group only.

## 11. Browser development mode

Run the API and Vite development server in separate terminals.

Terminal 1:

```powershell
python -m ottawa_rt.cli serve --config config/demo-4km-5m.yaml --host 127.0.0.1 --port 8000 --reload
```

Terminal 2:

```powershell
pnpm --dir web run dev
```

Open the Vite URL, normally <http://127.0.0.1:5173>. Vite proxies `/v1` and `/health` to port 8000. Restart the Vite process after changing `web/.env.local`.

`config/sionna-gui.yaml` is a technical scene/ray-settings handoff for Sionna inspection workflows. It is separate from the partner browser GUI and does not control the FastAPI/React application.

## 12. Docker, WSL2, DGX, and SLURM

Build the immutable image with an optional Cesium token:

```bash
export VITE_CESIUM_ION_TOKEN='your_token_here'
docker compose --profile local build
```

Prepare a queue and run the two local GPU workers:

```bash
docker compose run --rm api simulate \
  --config config/demo-4km-5m.yaml \
  --width-m 4000 \
  --run-id ottawa-4km-5m-multiband-20260919

RUN_ID=ottawa-4km-5m-multiband-20260919 \
  docker compose --profile local up worker-0 worker-1

docker compose run --rm api finalize \
  ottawa-4km-5m-multiband-20260919 \
  --config config/demo-4km-5m.yaml

docker compose --profile local up api
```

The compose file mounts `./data` at `/app/data`, so queues and results survive container replacement.

On a DGX, build or load the same image, mount the same `data/` tree, and run:

```bash
scripts/run-dgx-workers.sh ottawa-4km-5m-multiband-20260919 ottawa-sionna-rt:latest
```

The launcher discovers visible MIG instances first and otherwise uses visible physical GPU UUIDs. It starts one container per device and writes logs under `data/runs/<run-id>/worker-logs`. Re-run the benchmark on the DGX; larger VRAM can permit a larger scene, but no worker may depend on pooled memory.

SLURM submits the same one-GPU worker:

```bash
sbatch --array=0-7 scripts/slurm-worker.sh \
  ottawa-4km-5m-multiband-20260919 \
  ottawa-sionna-rt:latest
```

The shared `data/` mount must support atomic rename operations used by the queue.

## 13. Troubleshooting

| Symptom | Check |
| --- | --- |
| `Scene not found` | Run `build-scene` with exactly the same `--width-m`; scene names include the integer width. |
| `No normalized sectors found` | Run `fetch-data` and `normalize-ised` using the selected configuration. |
| Requested frequency unavailable | Use a bucket present in the normalized ISED records; the error reports available buckets. |
| Only one GPU is active | Confirm `compute.local_gpu_count`, `nvidia-smi`, and `CUDA_VISIBLE_DEVICES`. Each process intentionally uses one GPU. |
| Queue stops with failed jobs | Read the `error` field in `queue/failed/*.json`, correct the cause, then run `retry-failed`. |
| Queue has processing jobs but no workers | Confirm the recorded host/PID is dead before returning only stale records to `pending`. |
| GUI has no runs | Confirm the GUI uses the same `--config`/`data_root`, and each run contains a readable `run.json`. |
| Coverage control has no bands | Finalize the run or confirm tile/stitched `.npz` files exist and match the run manifest. |
| Coverage stays on loading | The first uncached calibrated view reports `Recalculating coverage X/Y bands` and can take several minutes. Ordinary requests/images time out after 45 seconds; builds without progress for three minutes show an error. Check the API log and use `Retry coverage`. Already-built maps do not wait for other runs to build. |
| Photo 3D unavailable | Add the Cesium token before `pnpm build`; restart after rebuilding. |
| Photo 3D has black polygonal holes | Confirm the status reaches `Google Photo 3D ready` with zero failed tiles. Photo mode must hide the Cesium globe and use adaptive tile LOD; do not render World Terrain beneath Google's global mesh or force overview `maximumScreenSpaceError` below 8. |
| Photo 3D remains on `loading` | Let the camera stop moving and read the pending/processing counters. A `degraded` status reports actual failed Google/Cesium tile requests rather than ordinary refinement. |
| Photo 3D stutters during a demo | Warm the exact camera views first, keep the tab open, and confirm the tile-cache status is below its configured budget. Do not expect a detailed 10 km area to remain resident at every zoom level. |
| RF overlay clips into Photo 3D | Photo mode uses a translucent analysis plane 35 m above terrain. This is a display offset only; the saved receiver height and RF values are unchanged. |
| Browser shows an old bundle | Hard-refresh the page or add a new `reload=` query value after rebuilding. |
| Web install reports a symlink error on D: | Keep `nodeLinker: hoisted` and `packageImportMethod: copy` in `web/pnpm-workspace.yaml`, then reinstall with `pnpm --dir web install --frozen-lockfile --force`. |
| Query falls back to free space | The coordinate/band is outside completed ray-traced cells or no compatible completed run was selected. |

Finish an operational change with:

```powershell
python -m ottawa_rt.cli validate --config config/demo-4km-5m.yaml
python -m pytest
pnpm --dir web build
```

## 14. Recalculate coverage after adding receiver measurements

Importing measurements validates and matches the readings. Run `calibrate` afterward to fit them against a completed simulation and recalculate its native-resolution cell maps:

```powershell
Set-Location -LiteralPath 'D:\Projects\Sionna_RT_OTTAWA'
.\.venv\Scripts\Activate.ps1
python -m ottawa_rt.cli import-measurements path/to/receiver-measurements.csv --config config/demo-6km-2p5m.yaml
python -m ottawa_rt.cli calibrate data/measurements/receiver-measurements.jsonl --config config/demo-6km-2p5m.yaml --run-id measurement-validation-6km-2p5m-20260925
```

Replace the CSV path and JSONL filename with your own input names. Calibration uses matched readings with finite ray-traced predictions, fits on the training split, and reports validation/test errors. The completed 56-band measurement-validation run is the recommended source for this measurement dataset. Additional bands do not necessarily increase usable fitting rows: unmatched readings and cells with no finite simulated signal are excluded.

`calibrate` now rebuilds per-band and combined RSS, RSRP, and SINR coverage under `data/runs/<run-id>/cache/calibrated/<version>/`. Sector corrections are applied before selecting the strongest server. SINR is recalculated from corrected signal and interference powers plus receiver noise. Original `tiles/` and `stitched/` results are preserved.

To rebuild maps with an existing compatible fit, without refitting measurements:

```powershell
python -m ottawa_rt.cli rebuild-calibrated-coverage measurement-validation-6km-2p5m-20260925 --config config/demo-6km-2p5m.yaml
```

The GUI defaults to **Measurement-calibrated** when a compatible fit exists. Use **Prediction model → Original simulation** to compare with the baseline; the same choice controls coordinate queries. Run discovery checks for new calibration versions every 15 seconds, and each version receives new raster/image cache keys. API clients can choose `calibrated=true` or `calibrated=false` on `/v1/coverage/...` and `/v1/query`.

For an uncached stitched map, `format=metadata` returns HTTP 202 with `status: building`, `completed_bands`, and `total_bands`, plus `Retry-After: 2`. Poll the same URL until HTTP 200 supplies the normal image metadata. The GUI handles this automatically. Builds run in the background and duplicate requests share one build; a failed build returns HTTP 500 with its error detail. JSON requests and the rebuild CLI remain synchronous.

These are measurement-fitted power corrections over the saved Sionna propagation. They do not change the physical path-gain map or fill cells where no ray-traced signal exists. Material, antenna-pattern, or geometry fitting requires changing those physical inputs and running Sionna again. A fit is applied only to runs sharing its scientific model version and scene.

## 15. Schedule calibrated UE and larger-area experiments

Use the [calibrated campaign guide](experiment-campaigns.md) for the density/condition matrix, hidden background startup, progress, immutable inputs and resume procedure. The supplied campaign first tests the measured 6 km scene, then builds a separate 10 km square with a 1 m receiver grid, runs 5,600 tile/frequency jobs, refits the original mobile-node data on that new scene, and repeats the UE comparisons.

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli prepare-campaign
.\.venv\Scripts\python.exe -m ottawa_rt.cli run-campaign
.\.venv\Scripts\python.exe -m ottawa_rt.cli campaign-status
```

If a supervisor is already running, use only the status command. `data/experiments/legget-cal-20261001/status.json` and per-task logs show progress; `comparison.csv` contains completed cases. The new dataset is under `data/campaigns/legget-10km-1m-20261001`, with its own `config/legget-10km-1m.yaml`. Use that configuration for large-run retry, stitching, queries, calibration and serving the GUI.

Resource allocation inside a UE simulation uses Sionna SYS PF. Campaign scheduling determines which simulation runs next. See [radio resource allocation and priority](network-simulation.md#radio-resource-allocation-and-ue-priority) for the policy currently implemented and its QoS limits.
