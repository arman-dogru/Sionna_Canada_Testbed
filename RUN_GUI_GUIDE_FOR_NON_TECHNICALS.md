# Sionna Canada Testbed: GUI Setup and Displaying Simulation Runs

This guide documents how to install, build, launch, and use the browser GUI for the Ottawa Sionna RT testbed, with emphasis on displaying completed simulation runs.

The examples assume the repository is located at:

```text
C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
```

The current 6 km configuration used in the examples is:

```text
config\demo-6km-2p5m.yaml
```

Example run:

```text
demo-6km-2p5m-multiband-20260918-r2
```

---

# 1. What the GUI displays

The project includes a browser application backed by the Python/FastAPI service.

The browser supports:

- Planning Map
- Planning 3D
- Photo 3D
- run selection
- frequency-band selection
- metric selection
- path gain
- RSS
- RSRP
- SINR
- station markers
- coordinate queries
- receiver-height queries
- ranked sector/service predictions
- confidence and fallback indicators
- calibration comparisons when calibration data exists

Selecting a run recenters the browser view on that run's saved geographic bounds.

The browser is a visualization and query layer. Ray tracing still uses the saved Sionna scene under:

```text
data\scenes\
```

The Photo 3D/Cesium presentation layer is not used as propagation geometry.

---

# 2. Important rule: use the same configuration

The GUI/API should be started with the same project configuration used for the run.

For the current 6 km run, use:

```text
config\demo-6km-2p5m.yaml
```

Do not launch the GUI with `config\demo-4km-5m.yaml` when you intend to inspect a run created with `config\demo-6km-2p5m.yaml`.

The configuration determines the project paths, including the data root where runs are discovered.

---

# 3. Prerequisites

The source-development setup requires:

- Python 3.11 or 3.12
- the project virtual environment
- Node.js
- pnpm 11.19 for the browser application
- the project's Python dependencies
- the web dependencies

From PowerShell:

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA

Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Verify Python:

```powershell
python --version
```

Verify pnpm:

```powershell
pnpm --version
```

Verify the CLI:

```powershell
python -m ottawa_rt.cli --help
```

---

# 4. Install the GUI dependencies

This normally only needs to be done once, or after the web lockfile changes.

From the repository root:

```powershell
pnpm --dir web install --frozen-lockfile
```

If the Python project itself has not yet been installed into the environment:

```powershell
python -m pip install -e ".[all,dev]"
```

---

# 5. Check which runs exist

Before starting the GUI:

```powershell
Get-ChildItem .\data\runs -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object Name, LastWriteTime
```

Example:

```text
Name                                      LastWriteTime
----                                      -------------
demo-6km-2p5m-multiband-20260918-r2       ...
demo-6km-2p5m-multiband-20260918          ...
demo-4km-5m-multiband-20260918            ...
```

A run intended for GUI display should contain a readable:

```text
data\runs\<run-id>\run.json
```

Check a specific run:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"

Test-Path "data\runs\$run\run.json"
```

Expected result:

```text
True
```

You can inspect the manifest with:

```powershell
Get-Content "data\runs\$run\run.json" -Raw
```

---

# 6. Finalize the run before expecting full coverage controls

A run can exist before it is ready for normal GUI coverage display.

The important final products are stored under:

```text
data\runs\<run-id>\stitched\
```

Typical finalized artifacts include:

```text
stitched\<frequency>MHz.npz
stitched\all-bands.npz
stitched\stitch-report.json
finalization.json
```

The GUI can discover a run from its `run.json`, but coverage/band controls depend on compatible completed tile or stitched products.

For a completed 6 km run, finalize with:

```powershell
python -m ottawa_rt.cli finalize `
  demo-6km-2p5m-multiband-20260918-r2 `
  --config config/demo-6km-2p5m.yaml
```

If workers are still active and you intentionally want finalization to wait:

```powershell
python -m ottawa_rt.cli finalize `
  demo-6km-2p5m-multiband-20260918-r2 `
  --config config/demo-6km-2p5m.yaml `
  --wait `
  --poll-seconds 30
```

Important:

`--wait` waits for pending and processing work. It does not automatically retry failed jobs.

Check the queue before finalization:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"
$q = "data\runs\$run\queue"

Get-ChildItem $q -Directory | ForEach-Object {
    [pscustomobject]@{
        State = $_.Name
        Jobs  = @(Get-ChildItem $_.FullName -File).Count
    }
}
```

The clean completion state is:

```text
pending       0
processing    0
failed        0
done          <all jobs>
```

---

# 7. Recommended GUI launch: production-style local build

This is the simplest way to use the GUI for normal run inspection.

## Terminal 1 — build the browser

From the repository root:

```powershell
pnpm --dir web build
```

Wait for the build to complete.

You only need to rebuild when:

- web source code changes
- `web/.env.local` changes
- Cesium settings change
- you pull a newer web application version

---

## Terminal 1 — start the API and GUI server

For the current 6 km project:

```powershell
python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000
```

Leave this terminal running.

Then open:

```text
http://127.0.0.1:8000
```

Useful service endpoints:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/v1/runs
```

---

# 8. Verify the API before debugging the GUI

In another PowerShell terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Then inspect the runs seen by the API:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/runs |
    ConvertTo-Json -Depth 10
```

This is one of the most useful diagnostics.

If your run is missing from `/v1/runs`, the problem is on the API/data side rather than the browser rendering side.

---

# 9. Display a run in the GUI

Once the server is running:

1. Open:

   ```text
   http://127.0.0.1:8000
   ```

2. Use the run selector.

3. Select:

   ```text
   demo-6km-2p5m-multiband-20260918-r2
   ```

4. The map should recenter to the saved bounds for that run.

5. Select a frequency band.

6. Select a metric such as:

   ```text
   Path Gain
   RSS
   RSRP
   SINR
   ```

7. Use the map/3D controls to inspect the resulting coverage layer.

If the run appears but no bands are available, verify that the run has been finalized and that stitched `.npz` files exist.

Check:

```powershell
Get-ChildItem "data\runs\demo-6km-2p5m-multiband-20260918-r2\stitched" -File
```

---

# 10. Inspect finalized run artifacts

For one run:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"

Get-ChildItem "data\runs\$run" -Force
```

Expected high-level content includes some or all of:

```text
run.json
surfaces\
queue\
tiles\
stitched\
visualization\
finalization.json
```

Inspect stitched bands:

```powershell
Get-ChildItem "data\runs\$run\stitched" -File |
    Select-Object Name, Length, LastWriteTime
```

Inspect visualization outputs:

```powershell
Get-ChildItem "data\runs\$run\visualization" -File |
    Select-Object Name, Length, LastWriteTime
```

Typical saved run artifacts are:

| Path | Purpose |
|---|---|
| `run.json` | run metadata, model version, scene, bounds, grid, bands, sectors, ray settings |
| `surfaces/` | terrain-draped receiver surfaces |
| `queue/` | durable job state, metrics, and failures |
| `tiles/*.npz` | raw tile/band results |
| `stitched/<frequency>MHz.npz` | full-area per-band arrays |
| `stitched/all-bands.npz` | aggregate strongest coverage |
| `stitched/stitch-report.json` | stitching diagnostics |
| `visualization/*-summary.png` | engineering reference figures |
| `visualization/visualization-report.json` | visualization metadata |
| `finalization.json` | finalization/completion state |

---

# 11. Browser development mode

Use development mode when modifying the React/browser GUI itself.

This uses two terminals.

## GUI Development Terminal 1 — API

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
.\.venv\Scripts\Activate.ps1

python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000 `
  --reload
```

Leave it running.

---

## GUI Development Terminal 2 — Vite

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA

pnpm --dir web run dev
```

Vite normally provides:

```text
http://127.0.0.1:5173
```

Open the Vite URL rather than port 8000 when doing frontend development.

The development server proxies:

```text
/v1
/health
```

to the API on port 8000.

---

# 12. Production mode versus development mode

## Normal run inspection

Use:

```powershell
pnpm --dir web build

python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

## Frontend development

Use:

Terminal 1:

```powershell
python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000 `
  --reload
```

Terminal 2:

```powershell
pnpm --dir web run dev
```

Then open:

```text
http://127.0.0.1:5173
```

---

# 13. Cesium / Photo 3D setup

The browser supports Cesium-backed 3D visualization, including Photo 3D using Google Photorealistic 3D Tiles through Cesium ion.

The token is compiled into the browser bundle.

It is not read dynamically by the Python process.

Create the local environment file:

```powershell
Copy-Item web\.env.example web\.env.local
```

Edit:

```text
web\.env.local
```

Set:

```env
VITE_CESIUM_ION_TOKEN=your_token_here
VITE_CESIUM_ENABLE_OSM_BUILDINGS=true
VITE_CESIUM_ENABLE_PHOTOREALISTIC=true
```

Then rebuild:

```powershell
pnpm --dir web build
```

Restart the Python server:

```powershell
python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000
```

Important:

```text
web\.env.local
```

should remain local and uncommitted.

Do not place the Cesium token in:

- project YAML
- screenshots
- run artifacts
- ZIP exports
- Git commits

If a token is exposed, revoke it and create a new one.

After changing `web/.env.local`, restart/rebuild the frontend before expecting the new values to appear.

---

# 14. GUI visual modes

The application provides three main visualization modes.

## Planning Map

The engineering-oriented 2D map view.

Use this for:

- coverage inspection
- stations
- bands
- metrics
- map-based engineering analysis
- coordinate queries

## Planning 3D

The reproducible planning-oriented 3D view using the project's public Ottawa geometry and terrain presentation.

Use this for:

- scene context
- building geometry
- terrain
- spatial interpretation of coverage

## Photo 3D

Presentation-oriented photorealistic 3D visualization through Cesium/Google tiles.

Important:

The photorealistic streamed geometry is not used by Sionna for propagation.

The actual simulation geometry remains the reproducible saved scene under:

```text
data\scenes\
```

---

# 15. Display a newly completed run

Assume the simulation run is:

```text
demo-6km-2p5m-multiband-20260918-r3
```

The complete workflow is:

## Step 1 — verify completion

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r3"
$q = "data\runs\$run\queue"

Get-ChildItem $q -Directory | ForEach-Object {
    [pscustomobject]@{
        State = $_.Name
        Jobs  = @(Get-ChildItem $_.FullName -File).Count
    }
}
```

Make sure:

```text
pending = 0
processing = 0
failed = 0
```

---

## Step 2 — finalize

```powershell
python -m ottawa_rt.cli finalize `
  $run `
  --config config/demo-6km-2p5m.yaml
```

---

## Step 3 — verify stitched output

```powershell
Get-ChildItem "data\runs\$run\stitched" -File
```

---

## Step 4 — start the GUI

```powershell
pnpm --dir web build

python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000
```

---

## Step 5 — open browser

```text
http://127.0.0.1:8000
```

Select the new run from the run selector.

---

# 16. The GUI does not show my run

Check these in order.

## Check 1 — run directory exists

```powershell
Get-ChildItem .\data\runs -Directory |
    Select-Object Name
```

---

## Check 2 — `run.json` exists

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"

Test-Path "data\runs\$run\run.json"
```

Must return:

```text
True
```

---

## Check 3 — API uses the correct configuration

For the 6 km run, the server should have been started with:

```powershell
--config config/demo-6km-2p5m.yaml
```

The GUI must resolve the same `data_root` that contains the run.

---

## Check 4 — API can see the run

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/runs |
    ConvertTo-Json -Depth 10
```

If the run is absent here, fix the API/data configuration before debugging the React browser.

---

## Check 5 — refresh the browser

Use a hard refresh:

```text
Ctrl+Shift+R
```

If necessary, rebuild:

```powershell
pnpm --dir web build
```

and restart the API.

---

# 17. Run appears, but there are no bands

This generally means the run manifest exists but usable coverage artifacts are not yet available.

Check:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"

Get-ChildItem "data\runs\$run\tiles" -File -ErrorAction SilentlyContinue

Get-ChildItem "data\runs\$run\stitched" -File -ErrorAction SilentlyContinue
```

If the run has finished but `stitched` products do not exist, finalize it:

```powershell
python -m ottawa_rt.cli finalize `
  $run `
  --config config/demo-6km-2p5m.yaml
```

---

# 18. Photo 3D does not work

Check:

```powershell
Get-Content web\.env.local
```

Verify that a Cesium token is set.

Then rebuild:

```powershell
pnpm --dir web build
```

Restart:

```powershell
python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000
```

Then hard-refresh the browser.

---

# 19. Browser shows an old version

After changing web code or environment values:

```powershell
pnpm --dir web build
```

Restart the API.

Then hard-refresh:

```text
Ctrl+Shift+R
```

For frontend development, restart the Vite process after changing:

```text
web\.env.local
```

---

# 20. API health troubleshooting

Check health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Check API documentation:

```text
http://127.0.0.1:8000/docs
```

Check run discovery:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/runs |
    ConvertTo-Json -Depth 10
```

If port 8000 is already occupied:

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, State, OwningProcess
```

Inspect the owning process:

```powershell
Get-Process -Id <PID>
```

---

# 21. Re-render saved engineering figures

The browser GUI is not the only way to inspect a completed run.

You can regenerate the saved engineering figures without retracing:

```powershell
python -m ottawa_rt.cli render-run `
  demo-6km-2p5m-multiband-20260918-r2 `
  --config config/demo-6km-2p5m.yaml
```

If the command supports the selected frequency for the run, render a specific band using:

```powershell
python -m ottawa_rt.cli render-run `
  demo-6km-2p5m-multiband-20260918-r2 `
  --config config/demo-6km-2p5m.yaml `
  --frequency-mhz 1960
```

The resulting images are written under:

```text
data\runs\<run-id>\visualization\
```

---

# 22. Quick-start GUI workflow

For an already finalized 6 km run, the normal workflow is:

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA

Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

pnpm --dir web install --frozen-lockfile

pnpm --dir web build

python -m ottawa_rt.cli serve `
  --config config/demo-6km-2p5m.yaml `
  --host 127.0.0.1 `
  --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Select the run and then select the desired band and metric.

---

# 23. Quick diagnostic checklist

If the GUI is not showing the expected data:

```text
[ ] Correct virtual environment activated
[ ] Correct YAML passed to `serve`
[ ] `data\runs\<run-id>\run.json` exists
[ ] Run is visible in `/v1/runs`
[ ] Simulation queue completed
[ ] No failed jobs remain
[ ] Run was finalized
[ ] `stitched\*.npz` exists
[ ] Web application was rebuilt after frontend/env changes
[ ] Browser was hard-refreshed
[ ] Cesium token configured if using Cesium-backed 3D/photo features
```

---

# 24. Recommended operating pattern

For normal work, keep simulation execution and GUI visualization conceptually separate:

```text
Simulation terminals
    -> create run
    -> execute jobs
    -> monitor queue
    -> finalize

GUI terminal
    -> build web bundle if required
    -> serve API/browser using same config
    -> inspect finalized run
```

You do not need to rerun the ray tracer to reopen an already finalized run.

As long as the `data\runs\<run-id>` artifacts and corresponding project data are intact, the API and GUI can rediscover and display the saved run.
