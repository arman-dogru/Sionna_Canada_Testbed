# Sionna Canada Testbed: Running and Monitoring Experiments

This guide documents the recommended three-terminal workflow for creating, launching, monitoring, and finalizing runs in the Sionna Canada Testbed repository on Windows PowerShell.

Repository root used in the examples:

```text
C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
```

The examples below use the 6 km × 6 km, 2.5 m receiver-cell configuration:

```text
config\demo-6km-2p5m.yaml
```

and the example run ID:

```text
demo-6km-2p5m-multiband-20260918-r2
```

---

## 1. Terminal layout

Use three PowerShell terminals:

| Terminal | Purpose | Keep running? |
|---|---|---|
| Terminal 1 | Launch and own the simulation | Yes |
| Terminal 2 | Monitor preparation and queue progress | Optional; safe to stop |
| Terminal 3 | Monitor GPU utilization | Optional; safe to stop |

Important:

- Do not press `Ctrl+C` in Terminal 1 unless you actually want to stop the simulation.
- `Ctrl+C` in Terminal 2 or Terminal 3 only stops the monitor running in that terminal.
- PowerShell variables such as `$run` are local to each terminal. Define them separately in each terminal.

---

## 2. Open the repository and activate the environment

In every terminal:

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
```

Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, use:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

The prompt should begin with:

```text
(.venv)
```

---

## 3. List existing runs

Before creating a new run, inspect the existing runs:

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

Use a unique run ID for a new experiment.

A useful naming pattern is:

```text
<experiment>-<date>-r<number>
```

Example:

```text
demo-6km-2p5m-multiband-20260918-r3
```

---

# Part I — Creating a New Configuration

## 4. Copy an existing configuration

For a new 6 km × 6 km, 2.5 m experiment based on the 4 km configuration:

```powershell
Copy-Item config\demo-4km-5m.yaml config\demo-6km-2p5m.yaml
```

Edit the new file so that its relevant fields are:

```yaml
project:
  name: ottawa-legget-drive
  model_version: demo-multiband-v4-6km-2p5m

area:
  min_width_m: 6000
  max_width_m: 6000
  step_width_m: 6000
  context_m: 750
  tile_size_m: 500
  overlap_m: 50
  cell_size_m: 2.5

compute:
  local_gpu_count: 2
```

For the current 6 km setup:

- Output width: 6000 m
- Context: 750 m on each side
- Total scene width: 7500 m
- Tile size: 500 m
- Receiver cell size: 2.5 m
- Number of tiles: 12 × 12 = 144
- With nine frequency groups: 144 × 9 = 1296 simulation jobs

---

# Part II — Building the Scene

## 5. Build the scene before launching the run

Run:

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli build-scene `
  --config config/demo-6km-2p5m.yaml `
  --width-m 6000 `
  --force
```

A successful build should report paths similar to:

```text
data\scenes\legget-6000m\scene.xml
data\scenes\legget-6000m\scene_metadata.json
```

For the current 6 km scene, a successful build reported approximately:

```text
building_count: 16504
triangle_count: 2266062
```

Do not start the simulation until the scene build succeeds.

---

# Part III — Clean Start

## 6. Stop old Sionna CLI processes before a fresh run

If previous runs were interrupted, first stop existing `ottawa_rt.cli` processes.

Run:

```powershell
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -match 'ottawa_rt\.cli'
    } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
```

Verify that none remain:

```powershell
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'ottawa_rt\.cli' } |
    Select-Object ProcessId, CommandLine
```

If the command prints nothing, there are no matching CLI processes.

For the cleanest restart after an interrupted run, use a new run ID instead of repeatedly launching the same partially interrupted run.

---

# Part IV — The Three-Terminal Workflow

# Terminal 1 — Launch the run

## 7. Start the simulation

Choose a unique run ID:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"
```

Launch:

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli simulate `
  --config config/demo-6km-2p5m.yaml `
  --width-m 6000 `
  --run-id $run `
  --execute
```

Leave Terminal 1 running.

Do not press `Ctrl+C` unless you intentionally want to terminate the run.

### What happens first

The command can initially appear quiet.

Before queue execution begins, the run prepares receiver surfaces. During this preparation phase, the simulation queue can still contain zero jobs.

For the current configuration, up to 144 receiver surfaces are expected.

After preparation completes, the run creates the job queue and launches workers for the configured local GPUs.

With:

```yaml
compute:
  local_gpu_count: 2
```

the intended execution uses two local GPU workers.

---

# Terminal 2 — Monitor progress

## 8. Activate the environment

In Terminal 2:

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
.\.venv\Scripts\Activate.ps1
```

Set the same run ID:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"
```

Remember: `$run` must be set separately in every PowerShell terminal.

---

## 9. Full live monitor

Use the following monitor. It shows receiver-surface preparation first and switches automatically to queue progress once jobs exist.

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"
$runDir = "data\runs\$run"
$q = "$runDir\queue"
$surfaces = "$runDir\surfaces"

while ($true) {
    Clear-Host

    $surfaceCount = @(
        Get-ChildItem "$surfaces\*.ply" -File -ErrorAction SilentlyContinue
    ).Count

    $done       = @(Get-ChildItem "$q\done\*.json"       -File -ErrorAction SilentlyContinue).Count
    $processing = @(Get-ChildItem "$q\processing\*.json" -File -ErrorAction SilentlyContinue).Count
    $pending    = @(Get-ChildItem "$q\pending\*.json"    -File -ErrorAction SilentlyContinue).Count
    $failed     = @(Get-ChildItem "$q\failed\*.json"     -File -ErrorAction SilentlyContinue).Count

    $total = $done + $processing + $pending + $failed

    Write-Host "Run: $run"
    Write-Host ""

    if ($total -eq 0) {
        Write-Host "Stage: PREPARING RECEIVER SURFACES"
        Write-Host "Surfaces: $surfaceCount / 144"
    }
    else {
        $pct = [math]::Round(100 * $done / $total, 1)

        Write-Host "Stage: SIMULATING"
        Write-Host "Done:       $done"
        Write-Host "Processing: $processing"
        Write-Host "Pending:    $pending"
        Write-Host "Failed:     $failed"
        Write-Host "Total:      $total"
        Write-Host "Progress:   $pct %"
    }

    Write-Host ""
    Write-Host "Updated: $(Get-Date -Format 'HH:mm:ss')"
    Write-Host "Ctrl+C stops ONLY this monitor."

    Start-Sleep -Seconds 5
}
```

During preparation you may see:

```text
Run: demo-6km-2p5m-multiband-20260918-r2

Stage: PREPARING RECEIVER SURFACES
Surfaces: 37 / 144
```

During simulation you may see:

```text
Stage: SIMULATING
Done:       18
Processing: 2
Pending:    1276
Failed:     0
Total:      1296
Progress:   1.4 %
```

You may stop this monitor at any time with:

```text
Ctrl+C
```

Stopping Terminal 2's monitor does not stop Terminal 1's simulation.

---

## 10. One-shot progress check

If you do not want a continuously refreshing monitor:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"
$q = "data\runs\$run\queue"

$done       = @(Get-ChildItem "$q\done"       -File -ErrorAction SilentlyContinue).Count
$processing = @(Get-ChildItem "$q\processing" -File -ErrorAction SilentlyContinue).Count
$pending    = @(Get-ChildItem "$q\pending"    -File -ErrorAction SilentlyContinue).Count
$failed     = @(Get-ChildItem "$q\failed"     -File -ErrorAction SilentlyContinue).Count

$total = $done + $processing + $pending + $failed
$pct = if ($total -gt 0) {
    [math]::Round(100 * $done / $total, 1)
} else {
    0
}

[pscustomobject]@{
    Run        = $run
    Done       = $done
    Processing = $processing
    Pending    = $pending
    Failed     = $failed
    Total      = $total
    Progress   = "$pct%"
}
```

---

## 11. Direct queue inspection

To count files in each queue state:

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r2"

Get-ChildItem "data\runs\$run\queue" -Directory | ForEach-Object {
    [pscustomobject]@{
        State = $_.Name
        Jobs  = @(Get-ChildItem $_.FullName -File).Count
    }
}
```

Expected queue directories include:

```text
pending
processing
done
failed
```

---

# Terminal 3 — Monitor the GPUs

## 12. Live GPU utilization

In Terminal 3:

```powershell
nvidia-smi -l 2
```

This refreshes approximately every two seconds.

For a two-GPU run, verify that both GPUs show active compute usage once the simulation stage begins.

Stop the GPU monitor with:

```text
Ctrl+C
```

This does not stop the simulation.

---

## 13. Check active worker processes

From Terminal 2 or Terminal 3:

```powershell
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -match 'ottawa_rt\.cli.*worker'
    } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine
```

For a healthy two-GPU execution, two worker processes should normally be present during the simulation stage.

---

# Part V — Understanding Run State

## 14. Run directory

A run is stored under:

```text
data\runs\<run-id>
```

Example:

```text
data\runs\demo-6km-2p5m-multiband-20260918-r2
```

Useful contents include:

```text
data\runs\<run-id>\
├── queue\
│   ├── pending\
│   ├── processing\
│   ├── done\
│   └── failed\
├── surfaces\
└── ...
```

The queue files are the source of truth for job progress.

---

## 15. Interpreting progress

### Preparation phase

```text
Total: 0
```

can be normal while receiver surfaces are still being prepared.

Use the combined monitor to check:

```text
Surfaces: N / 144
```

### Simulation phase

A healthy running state may look like:

```text
Done:       324
Processing: 2
Pending:    970
Failed:     0
Total:      1296
```

### Finished

For the current 1296-job configuration, the target state is:

```text
Done:       1296
Processing: 0
Pending:    0
Failed:     0
Total:      1296
Progress:   100 %
```

Any nonzero `Failed` count should be inspected before finalization.

---

# Part VI — Finalizing a Completed Run

## 16. Finalize

After all jobs are complete:

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli finalize `
  demo-6km-2p5m-multiband-20260918-r2 `
  --config config/demo-6km-2p5m.yaml
```

Use the same configuration file that was used to create and execute the run.

---

# Part VII — Stopping a Run

## 17. Intentionally stop all Sionna CLI processes

If you actually need to terminate the active simulation:

```powershell
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -match 'ottawa_rt\.cli'
    } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
```

Then verify:

```powershell
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'ottawa_rt\.cli' } |
    Select-Object ProcessId, CommandLine
```

An empty result means no matching processes remain.

Abrupt termination can leave jobs in `queue\processing`. Do not assume those jobs completed successfully.

For a clean new experiment after repeated interrupted starts, use a new run ID.

---

# Part VIII — Recommended Day-to-Day Procedure

## 18. Standard workflow

### Step 1 — Activate environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### Step 2 — Check existing runs

```powershell
Get-ChildItem .\data\runs -Directory |
    Sort-Object LastWriteTime -Descending |
    Select-Object Name, LastWriteTime
```

### Step 3 — Build or verify scene

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli build-scene `
  --config config/demo-6km-2p5m.yaml `
  --width-m 6000 `
  --force
```

### Step 4 — Choose a new run ID

```powershell
$run = "demo-6km-2p5m-multiband-20260918-r3"
```

### Step 5 — Launch in Terminal 1

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli simulate `
  --config config/demo-6km-2p5m.yaml `
  --width-m 6000 `
  --run-id $run `
  --execute
```

### Step 6 — Monitor in Terminal 2

Set the same `$run`, then use the combined preparation/queue monitor from Section 9.

### Step 7 — Monitor GPUs in Terminal 3

```powershell
nvidia-smi -l 2
```

### Step 8 — Finalize after completion

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli finalize `
  $run `
  --config config/demo-6km-2p5m.yaml
```

---

# Part IX — Quick Reference

## Terminal 1

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
.\.venv\Scripts\Activate.ps1

$run = "demo-6km-2p5m-multiband-20260918-r2"

.\.venv\Scripts\python.exe -m ottawa_rt.cli simulate `
  --config config/demo-6km-2p5m.yaml `
  --width-m 6000 `
  --run-id $run `
  --execute
```

Leave it running.

## Terminal 2

```powershell
cd C:\Users\adogr056\Documents\Codex\Sionna_RT_OTTAWA
.\.venv\Scripts\Activate.ps1

$run = "demo-6km-2p5m-multiband-20260918-r2"
```

Then run the monitor from Section 9.

## Terminal 3

```powershell
nvidia-smi -l 2
```

---

# Part X — Important Rules

1. Use a unique run ID for each fresh experiment.
2. Use the same configuration throughout scene creation, simulation, and finalization.
3. Do not `Ctrl+C` Terminal 1 unless you intend to stop the simulation.
4. `Ctrl+C` is safe in the monitoring terminals.
5. Define `$run` separately in every PowerShell terminal.
6. `Total: 0` can be normal during receiver-surface preparation.
7. Once simulation begins, monitor `pending`, `processing`, `done`, and `failed`.
8. Verify both GPU utilization and worker processes when diagnosing a stalled run.
9. Do not delete `data\runs` as part of cache cleanup.
10. Finalize only after pending and processing reach zero and failed jobs have been addressed.
