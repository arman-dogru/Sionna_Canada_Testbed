# Uplink and modulation experiments

The original Ottawa experiments and the pinned Legget campaign simulate **downlink** (base station to UE). `simulate-network` now also supports **uplink** (UE/sensor to base station), using Sionna SYS PUSCH instead of PDSCH. Each run models one direction. The existing campaign's settings remain downlink.

Use this workflow to compare NR modulation/coding, offered traffic, UE density, carrier width, noise figure, UE transmit power and TDD airtime on a saved Sionna RT environment. These are system-level predictions; they do not measure a live operator or implement a complete NR protocol stack. The [general network guide](network-simulation.md) covers placement, arrival models and outputs. [NVIDIA's PHY abstraction](https://nvlabs.github.io/sionna/sys/phy_abstraction.html) provides the BLER tables; its [NR MCS decoder](https://nvlabs.github.io/sionna/phy/nr/utils.html) distinguishes PUSCH and PDSCH tables. This checkout remains pinned to Sionna 2.1.0.

## Run a minute of sensor uplink traffic

From PowerShell in the repository root:

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli simulate-network `
  --config config/demo-4km-5m.yaml `
  --scenario config/network-uplink-sensors.yaml
```

The supplied scenario places 24 outdoor sensor UEs in a 1 km square. Each generates a 200-byte packet once a second for 60 simulated seconds. Half the slots are available to UL. Change `name` for each experiment; existing results are protected unless `--overwrite` is supplied. Simulated duration and wall-clock execution time differ.

To pick a sensor location, use the `ues` list described in the general guide, with local east/north coordinates and one entry for every profile member. For procedural UEs, omit that list and set the placement square, counts and seed. To use another completed environment, change both the project `--config` and scenario `run_id`/`frequency_mhz` to compatible inputs. Preparing new terrain still uses the [RT guide](../RUN_RT_GUIDE_FOR_NON_TECHNICALS.md).

The packets model payload size and timing. They do not transmit or parse the actual contents of a JSON/CSV sensor file. For periodic data, `offered_mbps = packet_bytes * 8 * packets_per_second / 1_000_000`. Include assumed protocol overhead in packet size if desired; the simulator does not calculate it automatically.

## Direction and MCS configuration

| Field | Meaning |
| --- | --- |
| `direction` | `downlink` (default, PDSCH) or `uplink` (PUSCH). |
| `downlink_fraction` | DL slot fraction. UL receives its complement. `0` allows dedicated UL and `1` dedicated DL; the selected direction must receive some slots. No guard/special slots are modeled. |
| `ue_tx_power_dbm` | UL total-power cap for a full occupied carrier, default 23 dBm. Grants use fixed power spectral density: N/B granted RBs transmit N/B of the full-carrier power. |
| `noise_figure_db` | Receiver NF: UE for DL, base station for UL. Defaults are assumptions, not hardware measurements. |
| `link_adaptation` | `adaptive` (default) selects MCS against `bler_target`; `fixed` forces one MCS even if it cannot decode. |
| `mcs_table_index` | Supported tables `1` and `2`, default `1`. Table 2 includes 256-QAM. |
| `fixed_mcs` | Required for `fixed`, omitted/null for `adaptive`. Table 1 indices 0–28; table 2 indices 0–27 (28 is reserved). |

For example, append these fields to a scenario to force UL 16-QAM:

```yaml
direction: uplink
calibrated: false
channel_source: paths
downlink_fraction: 0.5
ue_tx_power_dbm: 23
link_adaptation: fixed
mcs_table_index: 1
fixed_mcs: 12
```

The ready-made sweep uses these NR operating points, for either direction:

| Case | MCS table | MCS index | Bits/symbol | Target coding rate |
| --- | ---: | ---: | ---: | ---: |
| Adaptive | 1 | Selected per grant | 2/4/6 | Selected per grant |
| QPSK | 1 | 4 | 2 | 308/1024 |
| 16-QAM | 1 | 12 | 4 | 434/1024 |
| 64-QAM | 1 | 20 | 6 | 567/1024 |
| 256-QAM | 2 | 20 | 8 | 682.5/1024 |

An MCS changes modulation **and coding rate**. These cases compare NR operating points, not modulation alone at a controlled code rate. Higher-order modulation can offer more bits per symbol but can perform worse at insufficient SINR. Adaptive operation may never select high orders at a difficult location. To compare adaptation with access to 256-QAM, also run `link_adaptation: adaptive` with `mcs_table_index: 2`.

Arbitrary constellations, BPSK, uncoded BER/EVM, custom FEC or waveform-level receiver experiments require a separate Sionna PHY link simulation. This interface uses the installed NR BLER abstraction and does not claim those capabilities.

## Run or schedule the comparison

The supplied full-buffer benchmark runs five cases in each direction, with seeds 41 and 42: **20 sequential experiments, 60 simulated seconds each**.

```powershell
.\.venv\Scripts\python.exe scripts/run-modulation-experiments.py `
  --config config/demo-4km-5m.yaml `
  --scenario config/network-modulation.yaml `
  --prefix ottawa-modulation-trial1
```

All cases sharing a seed use identical procedural UE positions, association, traffic definitions and RT ray budgets. The runner requires `channel_source: paths`, checks placement/association equality and verifies link-power agreement within 0.00001 dB. GPU path records can be reordered between traces; the network model consumes their summed energy. Direction changes transmitted power, receiving noise and the interference model. Use equal DL/UL fractions for equal radio airtime, but do not interpret directional differences as modulation effects. Multiple seeds sample different positions and decode realizations; they are not repeated trials at one fixed location. Explicit UEs allow repeatable locations across different seeds.

For a quick one-second verification, append `--duration 1 --seeds 41`. To compare only DL, append `--directions downlink`; only UL uses `--directions uplink`. Supply a calibrated DL scenario with a pinned compatible model to perform calibrated DL comparisons. The runner refuses a calibrated UL scenario instead of silently clearing calibration.

To inspect the entire schedule first, append `--prepare-only`. The generated scenario YAMLs are saved under `data/experiments/<prefix>/scenarios/`. Run the same command without that flag to execute them. Each finished experiment writes its ordinary `data/network/<name>/` artifacts; `comparison.json` and `comparison.csv` checkpoint the completed comparison rows after every case. The runner stops on errors. A retry with `--overwrite` repeats all cases; partial schedules do not automatically resume.

For execution in the background:

```powershell
$modulationArgs = @('scripts/run-modulation-experiments.py',
  '--config', 'config/demo-4km-5m.yaml',
  '--scenario', 'config/network-modulation.yaml',
  '--prefix', 'ottawa-modulation-trial2')
Start-Process -FilePath '.\.venv\Scripts\python.exe' `
  -ArgumentList $modulationArgs -WorkingDirectory (Get-Location).Path `
  -WindowStyle Hidden -RedirectStandardOutput '.\outputs\modulation-trial2.stdout.log' `
  -RedirectStandardError '.\outputs\modulation-trial2.stderr.log'
```

Create `outputs` first if needed. Start once, then inspect logs/results; the runner's `modulation.lock` prevents two runners using the same prefix. After an unexpected stop, inspect the lock PID and its child/process command lines before removing a stale lock. Use the [campaign guide](experiment-campaigns.md) for the existing density/large-area supervisor. This standalone MCS runner does not modify that pinned campaign or its dependency order. Avoid concurrent GPU-heavy experiments on the same GPU.

## Read the results

`result.json` includes the exact scenario, input hashes, library versions, channel assumptions, per-UE results and a 100 ms traffic time series. The CSV now includes throughput, MCS and BLER alongside UE positions and allocations.

| Metric | Interpretation |
| --- | --- |
| `throughput_mbps` / `total_throughput_mbps` | Successfully served queued payload bits per simulated second, including bytes in partly transmitted packets. Excludes PHY/header overhead. |
| `goodput_mbps` / `total_goodput_mbps` | Payload of complete delivered application packets per simulated second. |
| `allocated_bandwidth_mhz` | Average occupied RB bandwidth across the entire run, including slots unavailable to this direction. |
| `radio_queue_latency_ms` | Arrival-to-completion mean/P50/P95/P99 for delivered packets. Includes queueing and slot service. |
| `estimated_e2e_latency_ms` | Delivered-packet delay plus assumed constant `core_latency_ms`. |
| `packet_loss_ratio` | Finite queue tail drops / offered packets. It is distinct from PHY block failures. |
| `pending_packets`, delivery ratio | Unfinished traffic at the horizon; pending traffic is not silently counted as packet loss. |
| `mean_predicted_tbler` | Sionna's expected transport-block failure probability averaged over scheduled grants. |
| `observed_tbler` | Failed / attempted scheduled transport blocks. Short runs have sampling uncertainty. |
| `mean_mcs`, `modulation_counts` | Mean MCS and scheduled grant counts by constellation; MCS means should be interpreted with the recorded table/direction. |
| `mean_scheduled_sinr_db` | Mean dB SINR over the UE's actual grants; UL changes with simultaneous transmitters. |
| `mean_tx_power_mw` | UL transmit power averaged over all slots; this is radio transmit power, not battery consumption. |

An undecodable fixed MCS still receives grants and records failures. Adaptive MCS uses the lowest available index when none can meet the BLER target; setting a target does not guarantee it. Queued bytes retry on later grants; there is no full HARQ process, combining or retransmission timing model. A low delivered-packet P95 with poor delivery/backlog does not establish low latency for all offered packets. Full-buffer latency depends on the artificial refill/buffer policy; use realistic sensor CBR or Poisson arrivals for application latency studies.

## Local verification on October 2, 2026

Ten one-second comparisons ran on the actual 4 km Ottawa scene with 12 full-buffer UEs, seed 41, a 20 MHz carrier, 30 kHz spacing, equal DL/UL airtime and the scenario's default powers/noise assumptions. These cases were **uncalibrated**, and short duration makes them functional checks rather than statistically converged rankings:

| Case | DL aggregate goodput | UL aggregate goodput |
| --- | ---: | ---: |
| Adaptive table 1 | 24.7392 Mbps | 4.1568 Mbps |
| Fixed QPSK | 6.0192 Mbps | 1.4880 Mbps |
| Fixed 16-QAM | 5.3376 Mbps | 3.8304 Mbps |
| Fixed 64-QAM | 7.3056 Mbps | 0 Mbps |
| Fixed 256-QAM | 0 Mbps | 1.7760 Mbps |

All ten cases had identical reduced RT link powers, UE positions and association. A repeated UL adaptive run reproduced every UE result and time-series point. A fresh default DL run reproduced every pre-extension UE metric and time-series point, confirming default behavior is unchanged. Results are under `data/experiments/ottawa-modulation-smoke-20261002/` and `data/network/ottawa-modulation-smoke-20261002-*/`; the verification record is `outputs/uplink-modulation-verification-20261002.json`.

Fixed-MCS failures are recorded even when goodput is zero. Do not infer that 256-QAM is intrinsically better than 64-QAM from the UL rows: coding, PF scheduling/history, link SINRs and short stochastic trials all affect these totals. Compare per-UE scheduled SINR, decoding failures, delivery and backlog over longer repeated trials at controlled positions.

The supplied **60-second sensor UL** scenario also completed on the real scene: 24 UEs offered 1,440 packets, delivered 780 (54.17%) and retained 660 pending, with zero buffer tail drops. Aggregate goodput was 0.0208 Mbps and delivered-packet radio/queue P95 was 33.55 ms (38.55 ms after the assumed 5 ms core delay). Observed transport-block failure ratio was 99.74% because weak queued UEs continued to receive retry grants. These uncalibrated, fixed-PSD assumptions are challenging for many positions; zero tail-drop loss does not mean successful delivery. Runtime was approximately 332 seconds on this CPU/GPU setup while the large campaign was active. Results are in `data/network/ottawa-uplink-sensors-60s/`.

The repository's 61 Python tests and six existing web tests passed; the results panel built successfully. It now labels UL/DL, adaptive/fixed MCS and table, and separates served throughput, complete-packet goodput and observed block failures.

## Uplink physics and calibration limits

UL traces the same static reciprocal propagation geometry at the selected frequency. It uses SISO path energy, the approximate BS directional receive gain and feeder loss, and an isotropic UE antenna. ISED BS transmit powers only establish DL association. In `channels.npz`, `rss_dbm` remains that DL association matrix; `reciprocal_gain_db` is the power-independent UL link-gain matrix. The stored CIRs retain the BS-to-UE trace ordering and are used through passive reciprocity, rather than being mislabeled as separate UE-to-BS traces.

Each cell allocates orthogonal RBs to its associated UEs. Only simulated UEs scheduled in another cell on the same RB contribute UL interference. Unscheduled UEs and DL BS emissions do not contribute to the UL model; there is no external background UL load, cross-link interference or mixed-direction neighboring cells. All modeled cells share the same carrier and TDD split. The fixed-PSD policy keeps each UE below its full-carrier cap; it is not a 3GPP open/closed-loop power-control implementation.

PF uses noise-limited achievable-rate estimates. After all cells schedule, the PHY uses actual shared-RB interference with perfect instantaneous knowledge to select/decode MCS. Effective SINR uses the minimum across granted RBs. Current flat rates normally grant a cell's carrier to one UE per slot, so granted RBs have uniform SINR; the reduction is conservative if that changes. This does not simulate an SC-FDMA waveform or frequency-selective scheduling.

Existing mobile-node CSVs measure DL RSRP, not base-station UL reception, UE transmit power or protocol latency. Applying their receiver correction to UL would be unjustified, so UL rejects `calibrated: true`. A validated UL calibration requires measurements at the receiving BS with known UE transmit power/antenna, frequency, bandwidth, positions and noise conditions. DL calibration continues to work for the existing campaigns and new DL MCS sweeps. At 3.5 GHz, the current campaign's receiver correction is also an extrapolation without training support in that frequency group.

For receiver-algorithm or modulation-only accuracy, build a Sionna PHY waveform experiment with explicit constellations, coding, channel/equalizer and BER/BLER measurement. For full protocol latency and application loss, integrate a complete 5G stack or a validated AODT worker with packet instrumentation; see [AODT configuration](aodt-configuration.md).
