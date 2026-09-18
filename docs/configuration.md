# Configuration reference

The simulation CLI uses one project YAML file for geography, tiling, RF behavior, defaults, source URLs, and local worker count. Keep project configurations under `config/`; `paths.data_root` is resolved from the repository root inferred from that location.

## Supplied profiles

| File | Output area | Cell size | Tile core | Context per side | Depth | Intended use |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `config/default.yaml` | Benchmark-selected, 2–12 km | 5 m | 1 km | 2 km | 5 | Baseline and final sizing. |
| `config/demo-overnight.yaml` | 2 km | 10 m | 1 km | 2 km | 4 | Small overnight/demo run. |
| `config/demo-4km.yaml` | 4 km | 10 m | 1 km | 1 km | 4 | Larger, lower-resolution demonstration. |
| `config/demo-4km-5m.yaml` | 4 km | 5 m | 500 m | 1 km | 4 | Current high-resolution multiband demonstration. |
| `config/sionna-gui.yaml` | N/A | 5 m | N/A | N/A | 5 | Technical Sionna scene-inspection handoff; not a CLI project config. |

Create a new project profile by copying the nearest YAML and changing `project.model_version`. Do not modify a profile associated with a completed run unless the old file is preserved with that run.

## `project`

```yaml
project:
  name: ottawa-legget-drive
  model_version: demo-multiband-v3-4km-5m
```

| Key | Meaning |
| --- | --- |
| `name` | Human-readable project identifier. |
| `model_version` | Immutable scientific model label copied into each `run.json`. Increment it whenever physical, numerical, or fallback assumptions change. |

## `paths`

```yaml
paths:
  data_root: data
```

`data_root` contains raw snapshots, normalized records, scenes, runs, measurements, and calibration. Use a host-mounted path or mounted object-store filesystem on DGX. Generated manifests store portable repository-relative POSIX paths.

## `anchor`

```yaml
anchor:
  address: "350 Legget Drive, Kanata, Ontario, K2K 2W7, Canada"
  latitude: 45.340455200216
  longitude: -75.91114196736
```

Latitude and longitude are WGS84 decimal degrees and define the center of output, scene-local coordinates, terrain origin, tile layout, and GUI camera. The address is descriptive; commands use the coordinates rather than geocoding at runtime.

Changing the anchor requires a new public-data clip, ISED normalization, scene, and run.

## `area`

```yaml
area:
  min_width_m: 4000
  max_width_m: 4000
  step_width_m: 4000
  context_m: 1000
  tile_size_m: 500
  overlap_m: 50
  cell_size_m: 5
```

| Key | Meaning and effect |
| --- | --- |
| `min_width_m` | Smallest output width evaluated by the benchmark estimator. |
| `max_width_m` | Largest candidate width and default acquisition sizing ceiling. This does not override `simulate --width-m`. |
| `step_width_m` | Increment between benchmark candidates. |
| `context_m` | Extra geometry outside every output edge. Total scene width is `output width + 2 × context_m`. Changing it requires rebuilding the scene. |
| `tile_size_m` | Non-overlapping owned output core for one job. Smaller tiles reduce per-job receiver-grid memory and increase job count. |
| `overlap_m` | Extra receiver margin around neighboring cores, retained for seam diagnostics but excluded from deterministic final ownership. It must be smaller than the tile size. |
| `cell_size_m` | Receiver-grid spacing. Halving it creates roughly four times as many cells for the same area. |

Useful calculations:

```text
tiles_per_axis = ceil(width_m / tile_size_m)
tile_count = tiles_per_axis²
job_count = tile_count × frequency_group_count
cells_per_axis = width_m / cell_size_m
```

Prefer a width exactly divisible by both `tile_size_m` and `cell_size_m` for predictable grid dimensions.

## `compute`

```yaml
compute:
  total_budget_hours: 12
  preparation_reserve_hours: 1.5
  max_vram_fraction: 0.90
  local_gpu_count: 2
  deterministic_seed: 41
```

| Key | Meaning |
| --- | --- |
| `total_budget_hours` | End-to-end time target used by benchmark sizing. It does not terminate workers. |
| `preparation_reserve_hours` | Budget held for acquisition, preprocessing, stitching, and validation. |
| `max_vram_fraction` | Maximum acceptable fraction of one device's memory during sizing. |
| `local_gpu_count` | Number of local workers launched by `simulate --execute`; GPUs are assigned from zero upward. |
| `deterministic_seed` | Base seed used to make per-job ray sampling repeatable. |

One worker uses one GPU or one MIG device. Increasing `local_gpu_count` increases job concurrency; it does not increase the memory available to any one scene.

## `receiver`

```yaml
receiver:
  default_height_agl_m: 1.5
  noise_figure_db: 7.0
  temperature_k: 290.0
```

| Key | Meaning |
| --- | --- |
| `default_height_agl_m` | Outdoor receiver height above the local terrain surface. Queries may override it. |
| `noise_figure_db` | Receiver noise figure used for noise and SINR estimates. |
| `temperature_k` | Thermal-noise reference temperature. |

Heights are AGL and terrain-relative. Do not insert ellipsoidal or orthometric absolute heights into this field.

## `simulation`

```yaml
simulation:
  max_depth: 4
  samples_per_tx: 1000000
  los: true
  specular_reflection: true
  diffuse_reflection: false
  refraction: true
  diffraction: true
  edge_diffraction: false
  min_free_space_rx_dbm: -140.0
  service_threshold_rsrp_dbm: -110.0
```

| Key | Meaning and tradeoff |
| --- | --- |
| `max_depth` | Maximum propagation interaction depth. More depth can capture additional paths and increases runtime. |
| `samples_per_tx` | Ray/path samples per transmitter in a tile job. Increase only after benchmarking quality and runtime. |
| `los` | Include direct line-of-sight paths. |
| `specular_reflection` | Include specular reflections. |
| `diffuse_reflection` | Include diffuse scattering. It is disabled in supplied profiles because of runtime cost. |
| `refraction` | Include transmission/refraction through supported materials. |
| `diffraction` | Include diffraction paths. |
| `edge_diffraction` | Enable the more expensive edge-diffraction option when supported. |
| `min_free_space_rx_dbm` | Conservative free-space upper-bound cutoff for including off-area sectors. More negative values include more sectors and cost more. |
| `service_threshold_rsrp_dbm` | Threshold used for service classification and calibration reporting. It does not remove weak simulated cells. |

`worker --samples-per-tx` and `worker --max-depth` are smoke-test overrides. Use them only with a dedicated smoke-run ID so production tiles do not contain mixed settings.

## `antenna_defaults`

```yaml
antenna_defaults:
  tx_power_dbm: 43.0
  front_to_back_db: 30.0
  height_agl_m: 25.0
  horizontal_beamwidth_deg: 65.0
  vertical_beamwidth_deg: 10.0
  gain_dbi: 15.0
  line_loss_db: 2.0
  downtilt_deg: 4.0
```

These values are applied only when an ISED field cannot be used. Every affected `SectorRecord` lists the fallback in `defaults_applied`; the substitution is never silent.

Power accounting uses the normalized power, gain, and line loss to derive EIRP. A change to a default requires re-running `normalize-ised` and creating a new run ID.

## `sources`

The URLs identify the ISED terrestrial extract and field descriptions, Ottawa LOD1 service, and NRCan HRDEM STAC search endpoint. `fetch-data` records the resolved resources, timestamps, licences, checksums, schemas, and processing parameters in `data/raw/provenance.json`.

Use `fetch-data --force` for a deliberate snapshot refresh. A refreshed ISED or geometry snapshot must receive a new run ID even when the YAML itself is unchanged.

## `materials`

```yaml
materials:
  ground: {itu_type: medium_dry_ground, thickness_m: 0.5, scattering_coefficient: 0.05}
  concrete: {itu_type: concrete, thickness_m: 0.2, scattering_coefficient: 0.10}
  glass: {itu_type: glass, thickness_m: 0.02, scattering_coefficient: 0.02}
  metal: {itu_type: metal, thickness_m: 0.01, scattering_coefficient: 0.15}
  vegetation: {itu_type: wood, thickness_m: 0.3, scattering_coefficient: 0.30}
  water: {itu_type: wet_ground, thickness_m: 1.0, scattering_coefficient: 0.02}
```

| Field | Meaning |
| --- | --- |
| `itu_type` | Sionna material/radio-material family. |
| `thickness_m` | Effective layer thickness used by the material model. |
| `scattering_coefficient` | Diffuse-scattering coefficient; it matters when diffuse reflection is enabled. |

The current public scene assigns ground to the terrain mesh and concrete to the LOD1 building envelope. The remaining profiles are reserved for future classified geometry. Sub-GHz carriers retain their actual frequency, while unsupported ITU-R P.2040 constitutive fits use the nearest documented boundary and record that fallback in tile metadata.

Changing a material requires `build-scene --force` and a new run ID.

## Which stages must be repeated?

| Change | Fetch | Normalize ISED | Build scene | New run ID |
| --- | :---: | :---: | :---: | :---: |
| Anchor or output region beyond current snapshot | Yes | Yes | Yes | Yes |
| ISED snapshot or source URL | Yes | Yes | No, unless geometry changed | Yes |
| Ottawa/terrain snapshot | Yes | No | Yes | Yes |
| Context or material profile | No, if source clip is already large enough | No | Yes, with `--force` | Yes |
| Tile size, overlap, or cell size | No | No | No | Yes |
| Receiver, path, sample, or service settings | No | No | Usually no | Yes |
| Antenna fallback | No | Yes | No | Yes |
| Local GPU count or runtime budget only | No | No | No | No |
| Cesium token or GUI feature flag | No | No | No | No; rebuild only the web app |

When in doubt, preserve the old YAML, increment `model_version`, and use a new run ID. This keeps measurements, calibration reports, and partner-facing comparisons auditable.
