"""Stationary outdoor UE traffic over saved Sionna RT channel powers."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time
from collections import deque
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import yaml

from ottawa_rt.calibrated_coverage import calibration_for_run, offset_db
from ottawa_rt.config import Settings
from ottawa_rt.geo import band_label, thermal_noise_dbm
from ottawa_rt.network_models import NetworkScenario, TrafficProfile
from ottawa_rt.simulation import frequency_bucket_mhz
from ottawa_rt.tiling import make_tiles


def load_scenario(path: Path) -> NetworkScenario:
    return NetworkScenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "p99": None}
    return {
        "mean": float(np.mean(values)),
        **{f"p{p}": float(np.percentile(values, p)) for p in (50, 95, 99)},
    }


class PacketQueue:
    """FIFO packets with finite buffers and exact partial-packet accounting."""

    def __init__(self, profile: TrafficProfile, rng: np.random.Generator):
        self.profile = profile
        self.rng = rng
        self.packets: deque[list[float]] = deque()
        self.next_arrival = 0.0
        self.offered_packets = 0
        self.dropped_packets = 0
        self.delivered_packets = 0
        self.served_bits = 0.0
        self.latencies_ms: list[float] = []

    @property
    def packet_bits(self) -> int:
        return self.profile.packet_bytes * 8

    @property
    def pending_bits(self) -> float:
        return sum(p[1] for p in self.packets)

    def arrive(self, until_s: float) -> None:
        if self.profile.arrival == "full_buffer":
            while len(self.packets) < self.profile.buffer_packets:
                self._append(until_s)
            return
        interval = self.packet_bits / (self.profile.offered_mbps * 1e6)
        while self.next_arrival < until_s:
            self._append(self.next_arrival)
            self.next_arrival += (
                self.rng.exponential(interval) if self.profile.arrival == "poisson" else interval
            )

    def _append(self, arrival_s: float) -> None:
        self.offered_packets += 1
        if len(self.packets) >= self.profile.buffer_packets:
            self.dropped_packets += 1
        else:
            self.packets.append([arrival_s, float(self.packet_bits)])

    def serve(self, bits: float, completion_s: float) -> float:
        served = 0.0
        while self.packets and bits > 0:
            packet = self.packets[0]
            used = min(bits, packet[1])
            packet[1] -= used
            bits -= used
            served += used
            if packet[1] <= 1e-7:
                self.packets.popleft()
                self.delivered_packets += 1
                self.latencies_ms.append(max(0.0, completion_s - packet[0]) * 1000)
        self.served_bits += served
        return served


class RadioMapSource:
    def __init__(self, settings: Settings, scenario: NetworkScenario):
        self.settings = settings
        self.scenario = scenario
        self.run_dir = settings.paths.runs / scenario.run_id
        manifest_path = self.run_dir / "run.json"
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        group = frequency_bucket_mhz(scenario.frequency_mhz)
        if group not in self.manifest["frequency_groups_mhz"]:
            raise ValueError(f"Run has no {group:g} MHz frequency group")
        self.suffix = f"{group:.1f}MHz".replace(".", "p") + ".npz"
        first_path = next((self.run_dir / "tiles").glob(f"*-{self.suffix}"), None)
        if first_path is None and scenario.channel_source == "radio_map":
            raise ValueError("No completed raw per-sector radio maps for this band")
        if scenario.channel_source == "radio_map":
            with np.load(first_path, allow_pickle=False) as first:
                self.tiles = make_tiles(
                    float(self.manifest["width_m"]),
                    float(first["tile_size_m"]),
                    float(first["overlap_m"]),
                )
        scene_xml = settings.resolve_path(self.manifest["scene_xml"])
        self.metadata_path = scene_xml.parent / "scene_metadata.json"
        self.metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        from pyproj import Transformer

        self.to_wgs = Transformer.from_crs(self.metadata["target_crs"], "EPSG:4326", always_xy=True)
        buildings = scene_xml.parent / "buildings.geojson"
        if not buildings.exists():
            raise FileNotFoundError("Outdoor placement requires the scene's buildings.geojson")
        from shapely.geometry import shape
        from shapely.ops import unary_union

        features = json.loads(buildings.read_text(encoding="utf-8"))["features"]
        self.buildings = unary_union([shape(f["geometry"]) for f in features])
        self.calibration = (
            calibration_for_run(
                settings,
                scenario.run_id,
                model_path=settings.resolve_path(scenario.calibration_model_path)
                if scenario.calibration_model_path
                else None,
            )
            if scenario.calibrated
            else None
        )
        if scenario.calibrated and self.calibration is None:
            raise ValueError("Requested calibration is not available for this RT run")
        self.sources = {
            settings.portable_path(p): _hash(p)
            for p in (manifest_path, self.metadata_path, buildings)
        }
        if self.calibration is not None:
            digest = hashlib.sha256(
                json.dumps(self.calibration, sort_keys=True).encode()
            ).hexdigest()
            self.sources["calibration_model_sha256"] = digest

    def calibration_evidence(self) -> dict[str, object] | None:
        if self.calibration is None:
            return None
        model = self.calibration
        counts = model.get("training_frequency_group_counts", {})
        baseline = model.get("baseline")
        if not counts and baseline:
            settings_path = self.settings.resolve_path(str(baseline))
            if settings_path.exists():
                from collections import Counter

                counts = dict(
                    Counter(
                        band_label(float(row["frequency_mhz"]))
                        for line in settings_path.read_text(encoding="utf-8").splitlines()
                        if line.strip() and (row := json.loads(line)).get("split") == "train"
                    )
                )
        group = band_label(self.scenario.frequency_mhz)
        support = int(counts.get(group, 0))
        return {
            "model_id": model["model_id"],
            "source_run_id": model.get("run_id"),
            "model_sha256": self.sources["calibration_model_sha256"],
            "method": model.get("method"),
            "metrics": model.get("metrics"),
            "measurement_provenance": model.get("measurement_provenance"),
            "training_frequency_group_counts": counts,
            "scenario_frequency_group": group,
            "scenario_group_training_count": support,
            "support": "frequency-group-supported" if support else "receiver-offset-extrapolation",
            "limitation": (
                "Power correction fitted to mobile-node RSRP; traffic, throughput and latency "
                "are not calibrated by those RF measurements. Group support does not prove "
                "each sector or UE location is independently validated."
            ),
        }

    def coordinate(self, x: float, y: float) -> tuple[float, float]:
        origin = self.metadata["local_origin"]
        lon, lat = self.to_wgs.transform(x + origin["easting"], y + origin["northing"])
        return float(lon), float(lat)

    def outdoor(self, x: float, y: float) -> bool:
        from shapely.geometry import Point

        return not self.buildings.intersects(Point(*self.coordinate(x, y)))

    def place(self, rng: np.random.Generator) -> list[dict[str, object]]:
        scenario = self.scenario
        half = float(self.manifest["width_m"]) / 2
        if (
            abs(scenario.center_x_m) + scenario.placement_width_m / 2 > half
            or abs(scenario.center_y_m) + scenario.placement_width_m / 2 > half
        ):
            raise ValueError("Placement area must fit inside the saved RT output region")
        specifications = (
            [u.model_dump() for u in scenario.ues]
            if scenario.ues is not None
            else [
                {"ue_id": f"{p.name}-{i + 1:03d}", "profile": p.name}
                for p in scenario.profiles
                for i in range(p.count)
            ]
        )
        for ue in specifications:
            if "x_m" not in ue:
                for _ in range(10000):
                    x, y = rng.uniform(-0.5, 0.5, 2) * scenario.placement_width_m
                    x += scenario.center_x_m
                    y += scenario.center_y_m
                    if self.outdoor(float(x), float(y)):
                        ue.update(x_m=float(x), y_m=float(y))
                        break
                else:
                    raise ValueError("Could not place an outdoor UE; change the placement area")
            x, y = float(ue["x_m"]), float(ue["y_m"])
            if not (-half <= x < half and -half <= y < half):
                raise ValueError(f"UE {ue['ue_id']} lies outside the saved RT output region")
            if not self.outdoor(x, y):
                raise ValueError(f"UE {ue['ue_id']} lies inside a building footprint")
            ue["longitude"], ue["latitude"] = self.coordinate(x, y)
        return specifications

    def sample(self, ues: list[dict[str, object]]) -> None:
        grouped: dict[str, list[dict[str, object]]] = {}
        for ue in ues:
            x, y = ue["x_m"], ue["y_m"]
            owner = next(
                (
                    t
                    for t in self.tiles
                    if t.center_x_m - t.size_m / 2 <= x < t.center_x_m + t.size_m / 2
                    and t.center_y_m - t.size_m / 2 <= y < t.center_y_m + t.size_m / 2
                ),
                None,
            )
            if owner is None:
                raise ValueError(f"No owning radio-map tile for UE {ue['ue_id']}")
            grouped.setdefault(owner.tile_id, []).append(ue)
        for tile_id, members in grouped.items():
            path = self.run_dir / "tiles" / f"{tile_id}-{self.suffix}"
            if not path.exists():
                raise FileNotFoundError(f"Incomplete RT coverage: {path.name}; no RF fallback used")
            self.sources[self.settings.portable_path(path)] = _hash(path)
            with np.load(path, allow_pickle=False) as data:
                rss = data["rss_dbm"].astype(np.float64)
                rsrp = data["rsrp_dbm"].astype(np.float64)
                ids, operators = data["sector_ids"].astype(str), data["operators"].astype(str)
                frequencies = data["frequencies_mhz"]
                if self.calibration is not None:
                    offsets = np.asarray(
                        [
                            offset_db(self.calibration, sid, float(f))
                            for sid, f in zip(ids, frequencies, strict=True)
                        ]
                    )
                    rss += offsets[:, None, None]
                    rsrp += offsets[:, None, None]
                eligible = np.ones(len(ids), dtype=bool)
                if self.scenario.operator:
                    eligible &= np.asarray(
                        [self.scenario.operator.lower() in o.lower() for o in operators]
                    )
                cell = float(data["cell_size_m"])
                total = float(data["tile_size_m"]) + 2 * float(data["overlap_m"])
                cx, cy = data["tile_center"]
                for ue in members:
                    col = int((ue["x_m"] - (cx - total / 2)) / cell)
                    row = int((ue["y_m"] - (cy - total / 2)) / cell)
                    powers = rss[:, row, col]
                    references = rsrp[:, row, col]
                    candidates = np.flatnonzero(
                        eligible & np.isfinite(powers) & np.isfinite(references)
                    )
                    ue.update(
                        rf_tile=path.name,
                        rf_row=row,
                        rf_column=col,
                        rf_cell_size_m=cell,
                        height_agl_m=float(data["receiver_height_agl_m"]),
                        z_local_m=float(data["receiver_z_m"][row, col]),
                        elevation_m=float(data["receiver_z_m"][row, col])
                        + float(self.metadata["local_origin"]["elevation_m"]),
                    )
                    if not len(candidates):
                        ue.update(
                            serving_sector_id=None,
                            sinr_db=None,
                            rsrp_dbm=None,
                            received_power_dbm=None,
                            operator=None,
                        )
                        continue
                    server = candidates[np.argmax(references[candidates])]
                    interference = sum(
                        10 ** (float(v) / 10)
                        for i, v in enumerate(powers)
                        if i != server and math.isfinite(v)
                    )
                    noise = 10 ** (
                        thermal_noise_dbm(
                            self.scenario.bandwidth_mhz,
                            self.scenario.noise_figure_db,
                        )
                        / 10
                    )
                    sinr = float(powers[server]) - 10 * math.log10(interference + noise)
                    ue.update(
                        serving_sector_id=str(ids[server]),
                        sinr_db=sinr,
                        rsrp_dbm=float(references[server]),
                        received_power_dbm=float(powers[server]),
                        operator=str(operators[server]),
                    )


def run_network(settings: Settings, scenario: NetworkScenario, *, overwrite: bool = False) -> dict:
    from ottawa_rt.network_backend import SionnaBackend, UplinkBackend

    started = time.perf_counter()
    output = settings.paths.data / "network" / scenario.name
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Experiment {scenario.name} exists; choose a new name or --overwrite"
        )
    source = RadioMapSource(settings, scenario)
    rng = np.random.default_rng(scenario.seed)
    ues = source.place(rng)
    channels = None
    trace = {"source": "saved-radio-map"}
    if scenario.channel_source == "paths":
        from ottawa_rt.network_channels import trace_ue_channels

        trace, channels = trace_ue_channels(source, ues)
    else:
        source.sample(ues)
    cell_ids = sorted({u["serving_sector_id"] for u in ues if u["serving_sector_id"] is not None})
    serving = np.asarray(
        [
            cell_ids.index(u["serving_sector_id"]) if u["serving_sector_id"] is not None else -1
            for u in ues
        ]
    )
    sinr = np.asarray([u["sinr_db"] if u["sinr_db"] is not None else -100 for u in ues])
    profiles = {p.name: p for p in scenario.profiles}
    queues = [
        PacketQueue(profiles[u["profile"]], np.random.default_rng(scenario.seed + i + 1))
        for i, u in enumerate(ues)
    ]
    import torch

    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        if scenario.direction == "uplink":
            channel_ids = channels["sector_ids"].tolist()
            gains = channels["reciprocal_gain_db"]
            selected_gains = np.asarray([gains[channel_ids.index(sid)] for sid in cell_ids])
            if not cell_ids:
                selected_gains = np.full((1, len(ues)), -np.inf)
            backend = UplinkBackend(scenario, serving, selected_gains)
        else:
            backend = SionnaBackend(scenario, serving, sinr)
        rb_sum = np.zeros(len(ues))
        mcs_sum = np.zeros(len(ues))
        tbler_sum = np.zeros(len(ues))
        attempts = np.zeros(len(ues), dtype=int)
        failures = np.zeros(len(ues), dtype=int)
        sinr_sum = np.zeros(len(ues))
        modulation_counts = [{} for _ in ues]
        from sionna.phy.nr.utils import decode_mcs_index

        modulation_orders, _ = decode_mcs_index(
            torch.arange(29 if scenario.mcs_table_index == 1 else 28),
            table_index=scenario.mcs_table_index,
            is_pusch=scenario.direction == "uplink",
        )
        modulation_orders = modulation_orders.cpu().numpy()
        time_series = []
        steps = round(scenario.duration_s / scenario.slot_s)
        if steps < 1 or not math.isclose(
            steps * scenario.slot_s, scenario.duration_s, abs_tol=1e-9
        ):
            raise ValueError("duration_s must be a positive whole number of NR slots")
        dl_slots = 0
        ul_slots = 0
        previous_delivered = 0
        interval = max(1, round(0.1 / scenario.slot_s))
        with torch.no_grad():
            for slot in range(steps):
                end = (slot + 1) * scenario.slot_s
                for queue in queues:
                    queue.arrive(
                        end if queue.profile.arrival != "full_buffer" else slot * scenario.slot_s
                    )
                downlink = math.floor((slot + 1) * scenario.downlink_fraction + 1e-9) > math.floor(
                    slot * scenario.downlink_fraction + 1e-9
                )
                serve_direction = downlink if scenario.direction == "downlink" else not downlink
                if serve_direction:
                    if scenario.direction == "downlink":
                        dl_slots += 1
                    else:
                        ul_slots += 1
                    bits, rb, mcs, tbler = backend.step(
                        np.asarray([bool(q.packets) for q in queues])
                    )
                    served = np.asarray(
                        [q.serve(float(b), end) for q, b in zip(queues, bits, strict=True)]
                    )
                    backend.feedback(served)
                    rb_sum += rb
                    mcs_sum += mcs
                    tbler_sum += tbler
                    attempts += rb > 0
                    failures += (rb > 0) & (backend.last_feedback == 0)
                    sinr_sum += np.where(rb > 0, backend.last_sinr_db, 0)
                    orders = modulation_orders[mcs.astype(int)]
                    for i in np.flatnonzero(rb > 0):
                        label = {2: "QPSK", 4: "16QAM", 6: "64QAM", 8: "256QAM"}[int(orders[i])]
                        modulation_counts[i][label] = modulation_counts[i].get(label, 0) + 1
                if (slot + 1) % interval == 0 or slot == steps - 1:
                    delivered = sum(q.delivered_packets * q.packet_bits for q in queues)
                    start_slot = slot // interval * interval
                    time_series.append(
                        {
                            "time_s": end,
                            "goodput_mbps": (delivered - previous_delivered)
                            / ((slot + 1 - start_slot) * scenario.slot_s * 1e6),
                            "pending_packets": sum(len(q.packets) for q in queues),
                            "dropped_packets": sum(q.dropped_packets for q in queues),
                        }
                    )
                    previous_delivered = delivered
    finally:
        torch.set_num_threads(old_threads)
    all_latencies = []
    for i, (ue, queue) in enumerate(zip(ues, queues, strict=True)):
        all_latencies.extend(queue.latencies_ms)
        ue.update(
            goodput_mbps=queue.delivered_packets * queue.packet_bits / scenario.duration_s / 1e6,
            offered_packets=queue.offered_packets,
            delivered_packets=queue.delivered_packets,
            dropped_packets=queue.dropped_packets,
            pending_packets=len(queue.packets),
            packet_loss_ratio=queue.dropped_packets / max(queue.offered_packets, 1),
            served_bits=queue.served_bits,
            throughput_mbps=queue.served_bits / scenario.duration_s / 1e6,
            pending_bits=queue.pending_bits,
            allocated_bandwidth_mhz=rb_sum[i] / steps * 12 * 15e3 * 2**scenario.numerology / 1e6,
            mean_mcs=float(mcs_sum[i] / attempts[i]) if attempts[i] else None,
            mean_predicted_tbler=float(tbler_sum[i] / attempts[i]) if attempts[i] else None,
            scheduled_transport_blocks=int(attempts[i]),
            failed_transport_blocks=int(failures[i]),
            observed_tbler=float(failures[i] / attempts[i]) if attempts[i] else None,
            mean_scheduled_sinr_db=float(sinr_sum[i] / attempts[i]) if attempts[i] else None,
            modulation_counts=modulation_counts[i],
            radio_queue_latency_ms=_percentiles(queue.latencies_ms),
            estimated_e2e_latency_ms=_percentiles(
                [v + scenario.core_latency_ms for v in queue.latencies_ms]
            ),
        )
        if scenario.direction == "uplink":
            ue["sinr_db"] = ue["mean_scheduled_sinr_db"]
            ue["mean_tx_power_mw"] = (
                10 ** (scenario.ue_tx_power_dbm / 10) * rb_sum[i] / scenario.num_rb / steps
            )
        # Conservation includes partially transmitted packets still in the queue.
        expected = queue.offered_packets * queue.packet_bits
        accounted = (
            queue.served_bits + queue.pending_bits + queue.dropped_packets * queue.packet_bits
        )
        if not math.isclose(expected, accounted, abs_tol=0.01):
            raise AssertionError(f"Packet accounting failed for {ue['ue_id']}")
    rates = np.asarray([u["goodput_mbps"] for u in ues])
    offered = sum(q.offered_packets for q in queues)
    dropped = sum(q.dropped_packets for q in queues)
    delivered = sum(q.delivered_packets for q in queues)
    result = {
        "schema_version": 1,
        "name": scenario.name,
        "created_at": datetime.now(UTC).isoformat(),
        "backend": "nvidia-sionna-sys",
        "device": backend.device,
        "scenario": scenario.model_dump(),
        "channel_trace": trace,
        "calibration": source.calibration_evidence(),
        "versions": {p: version(p) for p in ("sionna", "sionna-rt", "torch", "numpy")},
        "source_sha256": source.sources,
        "summary": {
            "ue_count": len(ues),
            "connected_ue_count": int((serving >= 0).sum()),
            "serving_cell_count": len(cell_ids),
            "total_goodput_mbps": float(rates.sum()),
            "total_throughput_mbps": sum(q.served_bits for q in queues) / scenario.duration_s / 1e6,
            "direction": scenario.direction,
            "scheduled_transport_blocks": int(attempts.sum()),
            "failed_transport_blocks": int(failures.sum()),
            "observed_tbler": float(failures.sum() / attempts.sum()) if attempts.sum() else None,
            "mean_predicted_tbler": float(tbler_sum.sum() / attempts.sum())
            if attempts.sum()
            else None,
            "mean_ue_goodput_mbps": float(rates.mean()),
            "p5_ue_goodput_mbps": float(np.percentile(rates, 5)),
            "jain_fairness": float(rates.sum() ** 2 / (len(rates) * (rates**2).sum()))
            if np.any(rates)
            else 0.0,
            "offered_packets": offered,
            "delivered_packets": delivered,
            "dropped_packets": dropped,
            "pending_packets": sum(len(q.packets) for q in queues),
            "packet_loss_ratio": dropped / max(offered, 1),
            "packet_delivery_ratio": delivered / max(offered, 1),
            "radio_queue_latency_ms": _percentiles(all_latencies),
            "estimated_e2e_latency_ms": _percentiles(
                [v + scenario.core_latency_ms for v in all_latencies]
            ),
            "channel_bandwidth_mhz_per_cell": scenario.bandwidth_mhz,
            "occupied_bandwidth_mhz_per_cell": scenario.num_rb
            * 12
            * 15e3
            * 2**scenario.numerology
            / 1e6,
            "resource_blocks_per_cell": scenario.num_rb,
            "downlink_slots": dl_slots,
            "uplink_slots": ul_slots,
            "wall_time_s": time.perf_counter() - started,
        },
        "assumptions": [
            "Stationary outdoor UEs; direct CIR at DTM-relative heights or explicitly selected saved RF cells.",
            "Wideband, frequency-flat SISO abstraction of path energies; no MIMO beamforming or frequency selectivity.",
            "Configured NR carrier bandwidth with fixed total received power; not measured operator capacity.",
            "All other finite sectors in the selected 5 MHz RF group are full-load co-channel interferers.",
            "Operator filter restricts serving cells; other operators remain in interference.",
            (
                f"NVIDIA PF scheduler, {scenario.link_adaptation} MCS table {scenario.mcs_table_index} "
                f"{'PUSCH' if scenario.direction == 'uplink' else 'PDSCH'}, stochastic decoding; perfect SINR knowledge."
            ),
            "FIFO packets with fragmentation and finite tail-drop buffers; failed blocks retry on later grants.",
            "No HARQ combining/timing, RLC/PDCP/IP/TCP stack, mobility, handover, or carrier aggregation.",
            "Arrivals are batched per slot; radio/queue delay has up to one slot of timing quantization.",
            "E2E delay adds only the configured constant core_latency_ms; delivered packets only; pending packets censored.",
            "Full-buffer offered load and latency depend on the configured buffer size.",
            "NR abstraction supports 5G/6G research; no standardized 6G air interface is modeled.",
        ],
        "ues": ues,
        "time_series": time_series,
    }
    if scenario.direction == "uplink":
        result["assumptions"][2:5] = [
            "Same-frequency passive channel reciprocity; BS antenna pattern and feeder loss used on reception; isotropic UE antenna.",
            "DL ISED powers choose association only. Mobile-node DL RSRP corrections are not applied to UL.",
            "Fixed UE PSD across occupied carrier; grant N/B RBs emits ue_tx_power_dbm scaled by N/B; no 3GPP power-control loop.",
            "In-cell orthogonal RBs; only simulated scheduled UEs in other cells interfere on shared RBs; no external UL background load.",
            "PF uses noise-limited rate estimates. Decoding uses actual shared-RB SINR, reduced to the minimum over granted RBs.",
            "Single-direction TDD experiment: UL serves the complement of downlink_fraction; no simultaneous DL traffic or cross-link interference.",
            "received_power_dbm is full-carrier UL power at the BS; rsrp_dbm remains the DL association reference; sinr_db is scheduled UL mean.",
        ]
    output.mkdir(parents=True, exist_ok=True)
    if channels is not None:
        np.savez_compressed(output / "channels.npz", **channels)
    elif overwrite:
        (output / "channels.npz").unlink(missing_ok=True)
    temp = output / f".result-{os.getpid()}.tmp"
    temp.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, output / "result.json")
    with (output / "ues.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "ue_id",
            "profile",
            "latitude",
            "longitude",
            "x_m",
            "y_m",
            "height_agl_m",
            "serving_sector_id",
            "sinr_db",
            "goodput_mbps",
            "throughput_mbps",
            "allocated_bandwidth_mhz",
            "mean_mcs",
            "mean_scheduled_sinr_db",
            "mean_predicted_tbler",
            "observed_tbler",
            "offered_packets",
            "delivered_packets",
            "dropped_packets",
            "pending_packets",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ues)
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [u["longitude"], u["latitude"], u["elevation_m"]],
            },
            "properties": u,
        }
        for u in ues
    ]
    (output / "ues.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, allow_nan=False),
        encoding="utf-8",
    )
    return result
