# Calibrated Legget campaign results

The `legget-cal-20261001` campaign completed on October 2, 2026, at **18:05 Toronto time**. It saved all **116 downlink traffic scenarios** and all **5,600 RF tile/frequency jobs**. The larger RF run is `legget-10km-1m-cal-20261001`: a 10 km × 10 km square around 350 Legget Drive, with a native 1 m receiver grid. These experiments use Sionna RT channels and Sionna SYS PHY abstraction and proportional-fair scheduling.

See the [campaign runbook](experiment-campaigns.md) for the pinned configuration, scheduling commands, traffic profiles and resume procedure. The results below describe this saved campaign; they are simulated outcomes rather than measured network capacity.

## Verification and saved evidence

All 123 dependency steps completed: 56 existing-scene scenarios, six large-scene preparation/RF/calibration stages, 56 matching large-scene scenarios, four area-wide scenarios, and the comparison report. The existing source is `measurement-validation-6km-2p5m-20260925`. Generated data remain outside Git; copy the data roots when transferring the testbed.

The runtime directory `data/experiments/legget-cal-20261001/` contains:

- `plan.json`, `status.json`, and `comparison.json` / `comparison.csv`: ordered inputs, completion state and all 116 results.
- `completion-audit.json`: scenario/input hashes, actual result-source hashes, calibration lineage, packet conservation, CIR archive CRC checks, RF tile existence and native raster headers.
- `grid-audit.json`: complete reads and CRC checks of the raw and calibrated 3505 MHz, 2120 MHz and combined RSRP arrays, including finite-cell counts.
- `large-calibration-report.json`: the fresh mobile-node fit and snapshot provenance.
- Supervisor/per-task logs and dated recovery records: interruptions and the checks performed before resuming.

The RF queue has 5,600 unique completed records, 400 per band across 14 bands, and no pending, processing or failed records. Both raw and calibrated stitches save 14 bands plus a combined raster. All 30 products have **10,000 × 10,000** native raster dimensions and **1 m** cell metadata. Full payload/CRC checks cover the selected RSRP arrays above; other raster arrays have header checks rather than a complete archive integrity scan.

Large RF products, receiver surfaces, calibration and UE results are under `data/campaigns/legget-10km-1m-20261001/`. Existing-scene UE results are under `data/network/`. Each scenario saves `result.json`, `channels.npz`, `ues.csv` and `ues.geojson`.

## Density comparisons

These are arithmetic means across seeds 41 and 42 for the `mixed` condition: five seconds, a **1 km placement square**, 3505 MHz, a 20 MHz hypothetical NR carrier, 75% downlink slots, and 7 dB receiver noise figure. Counts therefore equal UEs/km². One-third of UEs receive each video, interactive and sensor traffic profile.

| Scene | UEs/km² | Aggregate goodput, Mbps | Delivered packets | Buffer drops | Pending packets | Mean of radio/queue P95s, ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing 6 km | 12 | 9.14 | 41.27% | 29.95% | 4,381 | 872 |
| Existing 6 km | 24 | 11.06 | 26.00% | 38.43% | 10,862.5 | 2,746 |
| Existing 6 km | 60 | 9.60 | 12.13% | 47.64% | 30,644.5 | 3,609 |
| Existing 6 km | 120 | 12.18 | 8.07% | 51.28% | 61,720 | 3,830 |
| Large 10 km | 12 | 15.98 | 67.44% | 16.95% | 2,375.5 | 1,078 |
| Large 10 km | 24 | 22.54 | 51.36% | 23.01% | 7,829.5 | 2,165 |
| Large 10 km | 60 | 45.80 | 43.68% | 29.91% | 20,115.5 | 2,538 |
| Large 10 km | 120 | 53.58 | 26.51% | 39.93% | 50,940 | 3,069 |

Offered load grows with UE count. In the existing scene, aggregate goodput stays near 9–12 Mbps while delivery falls and queues fill. In the larger scene, aggregate goodput grows, but the delivered fraction still falls from 67.44% to 26.51%. Mean per-UE goodput falls from 1.33 to 0.45 Mbps; Jain fairness falls from 0.344 to 0.163. More aggregate traffic does not mean every UE receives better service.

The larger scene expands the transmitter set: 3505 MHz traces include 33 sectors, versus 10 in the existing scene, and it uses a different mobile-node fit. Its higher aggregate goodput is not evidence that changing coverage-grid spacing alone increases capacity or improves physical accuracy. Density changes also generate a new set of locations; paired conditions at the same density and seed retain the same placement.

## Condition comparisons

Each row averages eight scenarios equally: four UE counts × two seeds, within the same 1 km placement square. Delivery/drop percentages are means of scenario ratios, rather than ratios pooled across every packet. Latency is the mean of scenario P95s, rather than a pooled P95.

| Condition | Existing goodput, Mbps | Large goodput, Mbps | Large delivered | Large buffer drops | Large radio/queue P95, ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mixed baseline | 10.49 | 34.48 | 47.25% | 27.45% | 2,213 |
| Light, offered load × 0.05 | 2.91 | 4.19 | 75.90% | 0.00% | 22 |
| Overload, offered load × 10 | 13.05 | 71.07 | 13.56% | 82.49% | 2,537 |
| Wideband, 40 MHz | 20.26 | 38.65 | 52.16% | 24.75% | 1,859 |
| Noisy, 10 dB noise figure | 14.07 | 33.04 | 45.99% | 27.88% | 2,320 |
| Less downlink, 50% slots | 7.20 | 26.58 | 37.86% | 32.45% | 2,665 |
| Reference, 2120 MHz | 28.48 | 31.14 | 45.93% | 28.96% | 1,876 |

Light load produces no buffer drops, but pending packets remain: zero drops does not mean all packets were delivered. Overload raises aggregate goodput while losing most offered packets to full buffers. The 40 MHz cases increase large-scene mean goodput by about 12%, with total received power fixed. Reducing downlink slots lowers it by about 23%. The reference frequency changes propagation, available sectors and calibration support together, so it is not an isolated frequency-only comparison.

The **existing-scene noisy cases are non-monotonic**: their average goodput rises despite the higher noise figure. Paired UE locations and received powers match to numerical precision. Adaptive MCS and proportional-fair scheduling respond to the changed SINR, and these saved outcomes require further scheduler/PHY investigation. They do not establish that adding noise improves radio links. Retain this anomaly when reporting or comparing the testbed.

The four area-wide cases distribute 12 or 120 UEs across the full **100 km²** output region: 0.12 or 1.2 UEs/km². Their detailed results are in the comparison files. They have different locations and densities from the 1 km cases and should not be treated as matching-density counterparts.

| Area-wide UEs | UEs/km² | Mean goodput, Mbps | Mean delivered | Mean buffer drops | Mean pending packets | Mean radio/queue P95, ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 | 0.12 | 20.97 | 84.56% | 5.59% | 1,500 | 1,113 |
| 120 | 1.20 | 88.00 | 42.28% | 29.63% | 42,631 | 2,755 |

## Calibration and physical limits

The new fit, `calibration-20261002T142837Z`, uses the original seven mobile-node CSVs. The snapshot includes **24,775** canonical readings from **154,372** source rows. Only **160** readings survive matching and finite simulated-power requirements for fitting: 64 training, 45 validation and 51 held-out test readings. The audit verifies the original campaign input hashes, canonical/lineage snapshot hashes, baseline hash and measurement-provenance hash.

| Spatial split | Readings | MAE, dB | RMSE, dB |
| --- | ---: | ---: | ---: |
| Training | 64 | 12.90 | 17.21 |
| Validation | 45 | 12.92 | 15.80 |
| Test | 51 | 19.50 | 24.09 |

Test bias is +9.80 dB and the 90th-percentile absolute error is 40.93 dB. Training support is 30 sub-1 GHz, 31 at 1–2.3 GHz and three at 2.3–3.3 GHz; **there are no 3.3–4.2 GHz training readings**. The 3505 MHz scenarios therefore report `receiver-offset-extrapolation`. The 2120 MHz cases have broad frequency-group support. The existing fit's 15.14 dB test MAE comes from a different holdout and is not a directly comparable accuracy benchmark. Mobile-node RSRP calibrates received-power corrections, not scheduler behavior, traffic, throughput or latency.

The 1 m grid is a saved sampling grid. Terrain propagation geometry retains 10 m spacing and buildings retain public LOD1 geometry. At the current ray budget, finite RSRP appears in **1.59%** of 3505 MHz cells, **3.16%** of 2120 MHz cells and **9.89%** of combined-band cells, with the same finite counts before and after calibration. Missing cells are unsampled/non-finite simulation outputs, not demonstrated outages. The combined calibrated raster reaches +4.92 dBm estimated RSRP; inspect such extreme values before using absolute coverage predictions. No ray-budget convergence or outlier-correction study was performed in this campaign. Traffic scenarios trace CIRs directly at UE coordinates instead of substituting sparse raster gaps for outages.

One UE in each 120-UE area-wide case lands on native DTM nodata: `video-014` for seed 41 and `interactive-032` for seed 42. Recovery retains the original coordinates and reuses the already saved RF receiver-surface height from tile `r017-c016`. Results record `terrain_height_fallbacks`, surface hashes, per-UE nodata flags and a stated assumption; the new CSV exports include these flags. These heights inherit the RF surface's existing imputation/interpolation and are not validated ground measurements. Missing or invalid saved surfaces still stop execution. The completed RF products and mobile-node fit were not rebuilt or edited.

Saved stitch reports contain a stale ownership description saying “1 km tile cores”; the verified queue records and raster assembly use **500 m cores with 50 m overlap**. This is a metadata wording issue, not a change in native resolution.

The network model is stationary, outdoor, SISO, perfect-SINR, stochastic PHY abstraction with FIFO buffers. It does not decode waveforms or simulate HARQ combining, RLC/PDCP/IP/TCP, handovers or a measured core. Packet loss here means buffer drops; unserved packets remain pending at five seconds. Report latency together with delivery, drops and backlog: only delivered packets contribute to latency percentiles. Estimated end-to-end latency adds the configured **5 ms** core delay. Two seeds do not establish confidence intervals, and the results do not validate a standardized 6G air interface.

## Recovery and viewing

The first RF interruption and queue-claim fix are recorded in the [runbook](experiment-campaigns.md#runtime-recovery-on-october-2-2026). A later supervisor disappeared after 105 traffic results; no recorded error identified its cause. At 21:47 UTC, its PID and workers were confirmed absent, all pinned inputs/scenarios were rehashed, prior logs were preserved, and one supervisor resumed. At 21:55 UTC, the area-wide terrain check stopped cleanly after 114 results. The saved-height recovery passed the full **63-test Python suite** and Ruff before completion. Final results retain the original profiles, seeds, calibration, resolution and ray settings.

To view the larger saved dataset, run:

```powershell
.\.venv\Scripts\python.exe -m ottawa_rt.cli serve `
  --config config/legget-10km-1m.yaml --host 127.0.0.1 --port 8002
```

Open <http://127.0.0.1:8002/?run=legget-10km-1m-cal-20261001&coverage-frequency=3505&metric=rsrp&calibrated=true&view=map>, select measurement-calibrated coverage, and select a saved experiment under **UE performance**. The map can display a coarser preview for performance; the audit confirms that raw and calibrated artifacts remain native 1 m.
