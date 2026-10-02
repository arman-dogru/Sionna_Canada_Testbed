# Scheduling calibrated RF and UE experiments

The campaign command runs a saved, dependency-ordered schedule. It first tests the existing Ottawa scene, then prepares and simulates a larger scene, fits mobile-node calibration on that new run, and finally repeats the UE experiments. This is local simulation scheduling: it is separate from the MAC scheduler that allocates radio resources inside each experiment.

## Campaign scheduled on October 1, 2026

The checked-in plan is `config/campaign-legget-20261001.yaml`. It uses the completed `measurement-validation-6km-2p5m-20260925` scene and its real mobile-node fit `calibration-20260930T165946Z`. All 116 network scenarios require calibration and pin a specific calibration file. A missing or incompatible fit stops the campaign.

| Stage | Scheduled work |
| --- | --- |
| Existing data | 56 direct-channel UE scenarios on the 6 km / 2.5 m run. |
| Larger RF run | A 10 km × 10 km square centered on 350 Legget Drive, with a 1 m receiver/coverage grid and 1 km scene context on each side. |
| Fresh calibration | Reclip the seven original mobile-node CSVs to the 10 km footprint, match against its expanded ISED sector set, fit spatial training/validation/test splits, and rebuild corrected coverage. |
| Larger-scene UE comparison | Repeat all 56 scenarios on the new scene, plus four experiments placing UEs across the full 10 km square. |
| Comparison | Continuously updated JSON/CSV of completed scenarios, then a final report. |

The full schedule contains 123 dependency steps: 116 traffic scenarios, six larger-scene preparation/RF/calibration steps, and one final comparison. There is no clock-based start delay; the supervisor advances as each dependency succeeds.

The 10 km profile is `config/legget-10km-1m.yaml`. Its isolated `data_root` is `data/campaigns/legget-10km-1m-20261001`, so its raw data, sector normalization, calibration, scenes and results do not replace the existing dataset. It schedules 400 tile cores of 500 m, with 50 m overlap, across 14 RF groups: **5,600 tile/frequency jobs**. Bands were selected from matched mobile-node sector frequencies, plus 3505 and 3605 MHz for NR experiments; this is not an all-frequency survey.

The receiver grid is 10,000 × 10,000 cells, or 100 million locations per stitched band. The NRCan DTM has native 1 m samples. The propagation terrain mesh retains the current 10 m spacing, and buildings retain public LOD1 geometry. A 1 m output grid does not imply 1 m scene-geometry accuracy or dense ray hits. Inspect finite coverage and held-out errors; this schedule is not a ray-budget convergence study.

## Density and condition matrix

Each phase uses **12, 24, 60, and 120 UEs**, with seeds **41 and 42**, and a five-second traffic duration. The common comparison footprint is a 1 km square around the anchor, making these counts also 12, 24, 60 and 120 UEs/km². One-third of UEs have each traffic profile:

| Profile | Baseline arrivals | Offered traffic per UE | Packet size | Buffer |
| --- | --- | ---: | ---: | ---: |
| Video | CBR | 5 Mbps | 1,200 bytes | 1,000 packets |
| Interactive | Poisson | 1 Mbps | 600 bytes | 500 packets |
| Sensor | CBR | 0.05 Mbps | 200 bytes | 100 packets |

| Condition | Change from the common baseline |
| --- | --- |
| `mixed` | 3505 MHz; 20 MHz hypothetical NR carrier; 30 kHz spacing; 75% downlink slots; 7 dB receiver noise figure. |
| `light` | Offered traffic × 0.05. |
| `overload` | Offered traffic × 10. |
| `wideband` | Carrier bandwidth 40 MHz; total received power remains fixed. |
| `noisy` | Receiver noise figure 10 dB. This is a receiver assumption, not a new weather model. |
| `less-dl` | Downlink slot fraction 50%. |
| `reference` | Same baseline at 2120 MHz, where the existing fit has frequency-group training support. This remains a hypothetical NR carrier rather than a claim about an operator's deployed RAT. |

Four extra `area-wide` cases on the 10 km scene use 12 and 120 UEs, both seeds, and a 10 km placement square. Those densities are 0.12 and 1.2 UEs/km²; do not compare them as equal-density counterparts to the 1 km cases.

Paired conditions at the same count and seed use the same placement procedure. Density changes generate a different set of UEs. Two seeds expose some placement/traffic variability; they do not establish a confidence interval. Channels are traced directly at outdoor UE coordinates with 100,000 rays per transmitter and depth four; sparse radio-map samples are not substituted as physical outages.

## Mobile-node calibration and evidence

The existing fit was trained on **263** usable matched readings, with **112 validation** and **206 test** readings. Its test MAE is about **15.14 dB**. This uncertainty should accompany interpretations of the experiment results.

There are no usable 3.3–4.2 GHz training rows in that existing fitted baseline. The 3505 MHz scenarios therefore apply its fitted global receiver correction and any available sector correction, with `calibration.support: receiver-offset-extrapolation`. They are corrected using mobile-node data, but do not have independently demonstrated 3.5 GHz calibration accuracy. The 2120 MHz scenarios report `frequency-group-supported`; that label describes a broad group, not independent validation of every UE position or sector.

The fresh 10 km fit uses the original seven input files, rather than the already-clipped 6 km snapshot. Ambiguous/unmatched rows and non-finite simulated measurement cells remain excluded. All three spatial splits must contain usable readings before the larger-scene UE stage starts. Inspect `large-calibration-report.json` for the actual sample counts and errors; its future fit is not assumed to improve accuracy automatically.

Calibration changes received-power predictions. RSRP readings do not calibrate traffic demand, scheduler priorities, core delay, throughput or latency. Queue latency is delivered-packet latency; report it with delivery ratio, drops and pending packets.

Each campaign pins the existing calibration and hashes the plan, project profiles, base traffic template and original CSV inputs. New fits save training group/sector counts, baseline and measurement hashes, and snapshot lineage. Individual network results include the calibration ID, content hash, held-out metrics and frequency-group support. Completed results are retained; changed inputs require a new campaign name.

## Prepare, start, and inspect

Run these commands from the repository root in PowerShell:

```powershell
Set-Location D:\Projects\Sionna_RT_OTTAWA
.\.venv\Scripts\python.exe -m pip install -e ".[all,network,dev]"

# Generate and validate the immutable plan; no GPU computation yet.
.\.venv\Scripts\python.exe -m ottawa_rt.cli prepare-campaign `
  --campaign config/campaign-legget-20261001.yaml

# Run in the foreground, or resume from the last successful step.
.\.venv\Scripts\python.exe -m ottawa_rt.cli run-campaign `
  --campaign config/campaign-legget-20261001.yaml

# Read progress from another terminal.
.\.venv\Scripts\python.exe -m ottawa_rt.cli campaign-status `
  --campaign config/campaign-legget-20261001.yaml
```

To keep the supervisor running after closing the terminal, start one hidden process with saved logs:

```powershell
$campaignRoot = (Resolve-Path .).Path
$campaignRuntime = Join-Path $campaignRoot 'data/experiments/legget-cal-20261001'
$campaignProcess = Start-Process `
  -FilePath (Join-Path $campaignRoot '.venv/Scripts/python.exe') `
  -ArgumentList '-m','ottawa_rt.cli','run-campaign','--campaign','config/campaign-legget-20261001.yaml' `
  -WorkingDirectory $campaignRoot -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $campaignRuntime 'supervisor.stdout.log') `
  -RedirectStandardError (Join-Path $campaignRuntime 'supervisor.stderr.log')
$campaignProcess.Id
```

Only one supervisor may own a campaign. Individual UE scenarios run sequentially in separate Python processes. The RF stage launches one worker per configured GPU, recycling each after four tile jobs to release process-local GPU memory. The larger profile also traces at most four transmitters at a time within a tile, then merges their per-sector powers before computing interference. Its batch seed is the configured seed plus the first transmitter index. VRAM does not pool between the two RTX 3070s. RF failures stop downstream stages rather than producing partially calibrated comparisons.

The campaign survives closing a terminal, but the computer must stay on and awake. After a restart, inspect the lock PID and any `queue/processing` claims before resuming. Remove `supervisor.lock` only after confirming that its supervisor and children are gone; an active lock is not a stale lock. For orphan RF claims, confirm `claimed_by` PIDs are gone and move only those records back to `queue/pending`. See the [queue recovery runbook](operations.md).

Use `--max-tasks 1` with `run-campaign` to execute one pending step during validation. The remaining steps stay queued; start the supervisor without that limit to continue. Resume does not automatically retry failed RF records; inspect/fix the error and use `retry-failed` with the **large project profile** before resuming.

## Results and visualization

`data/experiments/legget-cal-20261001/` contains:

- `plan.json`: full ordered schedule, input hashes and scenario hashes.
- `status.json`: current task, completed task IDs, supervisor PID, errors and RF queue counts.
- `scenarios/*.yaml`: every concrete UE experiment.
- `logs/*.log` and supervisor logs: per-scenario and per-GPU diagnostics.
- `comparison.csv` / `comparison.json`: completed results, updated during execution.
- `large-calibration-report.json`: fresh fit and mobile-node snapshot provenance when ready.

Existing-scene UE results are in `data/network/`. Larger-scene results are in `data/campaigns/legget-10km-1m-20261001/network/`. Open the GUI with the matching project configuration:

```powershell
# Existing data, including the first calibrated sweep.
.\.venv\Scripts\python.exe -m ottawa_rt.cli serve --config config/demo-6km-2p5m.yaml --port 8000

# Separate API/GUI for the larger dataset, once its scene/run exists.
.\.venv\Scripts\python.exe -m ottawa_rt.cli serve --config config/legget-10km-1m.yaml --port 8002
```

Choose the appropriate RF run and saved UE experiment in **UE performance**. For calibrated coverage, choose **Measurement-calibrated**. Large coverage products can take substantially longer to load than the existing 6 km products.

## Schedule your own campaign

Copy the campaign YAML, choose a new `name` and `large_run_id`, and supply matching project profiles, a compatible measured fit, and original mobile-node CSVs. Set `ue_counts` to multiples of three up to 498, choose reproducible seeds, and ensure the primary/reference frequencies are included in the RF group list. Generate the plan before starting GPU work. The six predefined condition variants are defined in `ottawa_rt.campaign.CONDITIONS`.

For a single custom experiment, copy `config/network-ottawa.yaml`, change its name, source run, counts, load or radio settings, and set:

```yaml
calibrated: true
calibration_model_path: data/calibration/calibration-20260930T165946Z.json
```

That example fit is compatible with the existing 6 km scene. Use the fresh `campaign-model.json` under the larger dataset for its scene. Run `simulate-network --config ... --scenario ...` as described in the [network simulation guide](network-simulation.md). Changing the calibration source does not make an incompatible model transferable across scenes.

The application also has a scheduled follow-up attached to the originating chat to inspect this campaign, recover routine issues when possible, and report completion or a failure requiring attention. Its follow-up is separate from the Python supervisor; simulation dependencies and results remain in the repository runtime artifacts.

## Verification before the long RF stage

All 41 Python tests pass, including calibration pinning, input immutability, dependency order, isolated data roots and failed-stage resume. The first real calibrated 12-UE, five-second case completed with 11.20576 Mbps aggregate goodput; it records the existing measured fit and the 3.5 GHz extrapolation label.

The four-transmitter RF implementation was also checked on an existing 6 km center tile using a **600 × 600** terrain-relative **1 m** receiver surface, **24 transmitters**, **one million rays per transmitter**, and **depth four**. It completed on GPU 1 with the CUDA polarized variant, yielding 137,651 receiver cells with finite signal. That checks the surface and batching path on an 8 GB RTX 3070; the full 10 km scene and its future fit still require their scheduled execution.

To reproduce that preflight after the existing source dataset is available:

```powershell
$env:CUDA_VISIBLE_DEVICES = '1'
.\.venv\Scripts\python.exe scripts/validate-campaign-rf.py
Remove-Item Env:CUDA_VISIBLE_DEVICES
```

The preflight writes temporary meshes/results under `.tmp/campaign-rf-smoke/` and its report to `outputs/campaign-rf-preflight.json`. It does not change completed RF tiles or measurement inputs.
