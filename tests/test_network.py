import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ottawa_rt.api import create_app
from ottawa_rt.config import load_settings
from ottawa_rt.network import PacketQueue, RadioMapSource, run_network
from ottawa_rt.network_models import NetworkScenario, TrafficProfile


def scenario(**updates):
    return NetworkScenario.model_validate(
        {
            "name": "test-network",
            "run_id": "rf",
            "frequency_mhz": 3505,
            "channel_source": "radio_map",
            "duration_s": 0.02,
            "device": "cpu",
            "placement_width_m": 20,
            "profiles": [{"name": "data", "count": 2, "offered_mbps": 1}],
            **updates,
        }
    )


def test_packet_conservation_with_partial_service_and_tail_drop():
    profile = TrafficProfile(
        name="data", count=1, offered_mbps=0.008, packet_bytes=100, buffer_packets=2
    )
    queue = PacketQueue(profile, np.random.default_rng(1))
    queue.arrive(0.35)
    assert queue.offered_packets == 4
    assert queue.dropped_packets == 2
    assert queue.serve(400, 0.35) == 400
    assert queue.delivered_packets == 0
    assert queue.serve(900, 0.4) == 900
    assert queue.delivered_packets == 1
    assert queue.latencies_ms == [400]
    assert queue.pending_bits == 300
    assert queue.offered_packets * queue.packet_bits == (
        queue.served_bits + queue.pending_bits + queue.dropped_packets * queue.packet_bits
    )


def test_seeded_poisson_and_full_buffer_accounting():
    profile = TrafficProfile(name="data", count=1, arrival="poisson", offered_mbps=0.1)
    queues = [PacketQueue(profile, np.random.default_rng(123)) for _ in range(2)]
    for queue in queues:
        queue.arrive(1)
    assert list(queues[0].packets) == list(queues[1].packets)
    profile = TrafficProfile(name="bulk", count=1, arrival="full_buffer", buffer_packets=3)
    queue = PacketQueue(profile, np.random.default_rng(1))
    queue.arrive(0)
    queue.serve(queue.packet_bits * 1.5, 0.01)
    queue.arrive(0.02)
    assert len(queue.packets) == 3
    assert queue.offered_packets == 4
    assert queue.dropped_packets == 0
    assert queue.served_bits + queue.pending_bits == queue.offered_packets * queue.packet_bits


@pytest.mark.parametrize(
    "updates",
    [
        {"name": "../escape"},
        {"numerology": 0, "bandwidth_mhz": 100},
        {"duration_s": float("nan")},
        {"ues": []},
        {"link_adaptation": "fixed"},
        {"fixed_mcs": 4},
        {"link_adaptation": "fixed", "fixed_mcs": 28, "mcs_table_index": 2},
        {"direction": "uplink"},  # DL radio maps are not UL channels
        {"direction": "uplink", "channel_source": "paths", "calibrated": True},
        {"direction": "uplink", "channel_source": "paths", "downlink_fraction": 1},
    ],
)
def test_scenario_rejects_invalid_input(updates):
    with pytest.raises(ValidationError):
        scenario(**updates)


@pytest.fixture
def project(tmp_path):
    pytest.importorskip("pyproj")
    pytest.importorskip("shapely")
    config = tmp_path / "config" / "default.yaml"
    config.parent.mkdir()
    config.write_text(Path("config/default.yaml").read_text())
    settings = load_settings(config)
    settings.paths.ensure()
    scene = settings.paths.scenes / "test"
    scene.mkdir()
    (scene / "scene_metadata.json").write_text(
        json.dumps(
            {
                "target_crs": "EPSG:26918",
                "local_origin": {"easting": 428614, "northing": 5021175, "elevation_m": 80},
            }
        )
    )
    (scene / "buildings.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    run = settings.paths.runs / "rf"
    (run / "tiles").mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps(
            {
                "run_id": "rf",
                "scene_xml": "data/scenes/test/scene.xml",
                "width_m": 20,
                "frequency_groups_mhz": [3505],
            }
        )
    )
    np.savez_compressed(
        run / "tiles/r000-c000-3505p0MHz.npz",
        rss_dbm=np.full((2, 2, 2), -65.0),
        rsrp_dbm=np.asarray([np.full((2, 2), -85), np.full((2, 2), -95)]),
        sector_ids=np.asarray(["server", "other-operator"]),
        operators=np.asarray(["TEST", "OTHER"]),
        frequencies_mhz=np.asarray([3505, 3505]),
        tile_size_m=20,
        overlap_m=0,
        tile_center=np.asarray([0, 0]),
        cell_size_m=10,
        receiver_height_agl_m=1.5,
        receiver_z_m=np.full((2, 2), 1.5),
    )
    return settings


def test_operator_filter_keeps_other_operator_interference_and_missing_tile_fails(project):
    selected = scenario(operator="TEST")
    source = RadioMapSource(project, selected)
    ues = source.place(np.random.default_rng(1))
    source.sample(ues)
    assert all(u["serving_sector_id"] == "server" for u in ues)
    assert all(-0.01 < u["sinr_db"] < 0 for u in ues)  # equal-power OTHER interferes
    assert all(u["height_agl_m"] == 1.5 for u in ues)
    (project.paths.runs / "rf/tiles/r000-c000-3505p0MHz.npz").unlink()
    with pytest.raises(FileNotFoundError, match="no RF fallback"):
        source.sample(ues)


def test_actual_sionna_scheduler_survives_idle_history_and_respects_cell_budget():
    torch = pytest.importorskip("torch")
    pytest.importorskip("sionna.sys")
    from ottawa_rt.network_backend import SionnaBackend

    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        backend = SionnaBackend(
            scenario(), np.asarray([0, 0, 1, -1]), np.asarray([15, -100, 15, -100])
        )
        total = np.zeros(4)
        for _ in range(2200):
            bits, rb, _, _ = backend.step(np.ones(4, dtype=bool))
            assert rb[0] <= 51 and rb[2] <= 51
            assert rb[1] == 0 and rb[3] == 0
            total += bits
            backend.feedback(bits)
        assert total[0] > 1e6 and total[2] > 1e6
        assert torch.isfinite(backend.scheduler.pf_metric).all()
        bits, rb, _, _ = backend.step(np.zeros(4, dtype=bool))
        assert not np.any(bits) and not np.any(rb)
    finally:
        torch.set_num_threads(old_threads)


def test_reproducible_end_to_end_saved_rf_traffic_and_api(project):
    pytest.importorskip("torch")
    pytest.importorskip("sionna.sys")
    selected = scenario()
    first = run_network(project, selected)
    with pytest.raises(FileExistsError):
        run_network(project, selected)
    second = run_network(project, selected, overwrite=True)
    assert first["ues"] == second["ues"]
    assert first["time_series"] == second["time_series"]
    assert first["summary"]["total_goodput_mbps"] > 0
    for ue in second["ues"]:
        assert (
            ue["offered_packets"]
            == ue["delivered_packets"] + ue["dropped_packets"] + ue["pending_packets"]
        )
        stats = ue["radio_queue_latency_ms"]
        e2e = ue["estimated_e2e_latency_ms"]
        if stats["p95"] is not None:
            assert e2e["p95"] - stats["p95"] == pytest.approx(5)
    client = TestClient(create_app(project))
    assert len(client.get("/v1/network?run_id=rf").json()) == 1
    assert client.get("/v1/network?run_id=other").json() == []
    assert client.get("/v1/network/test-network").json()["backend"] == "nvidia-sionna-sys"
    assert len(client.get("/v1/network/test-network/ues").json()["features"]) == 2
    assert client.get("/v1/network/test-network/csv").text.startswith("ue_id,profile")
    assert client.get("/v1/network/missing").status_code == 404
    assert client.get("/v1/network/bad%5Cpath").status_code == 400


@pytest.mark.parametrize("direction", ["downlink", "uplink"])
@pytest.mark.parametrize("table,index,order", [(1, 4, 2), (1, 12, 4), (1, 20, 6), (2, 20, 8)])
def test_forced_mcs_attempts_undecodable_links_and_reports_modulation(
    direction, table, index, order
):
    torch = pytest.importorskip("torch")
    pytest.importorskip("sionna.sys")
    from sionna.phy.nr.utils import decode_mcs_index

    from ottawa_rt.network_backend import SionnaBackend

    selected = scenario(
        direction=direction,
        channel_source="paths",
        link_adaptation="fixed",
        fixed_mcs=index,
        mcs_table_index=table,
    )
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        backend = SionnaBackend(selected, np.asarray([0, 1]), np.asarray([-100, 60]))
        bits, rb, mcs, tbler = backend.step(np.ones(2, dtype=bool))
        assert list(rb) == [51, 51]
        assert list(mcs) == [index, index]
        assert bits[0] == 0 and bits[1] > 0
        assert tbler[0] == 1 and tbler[1] == 0
        assert list(backend.last_feedback) == [0, 1]
        orders, _ = decode_mcs_index(
            torch.as_tensor(mcs), table_index=table, is_pusch=direction == "uplink"
        )
        assert list(orders) == [order, order]
    finally:
        torch.set_num_threads(old_threads)


def test_uplink_scheduled_interference_and_power_budget():
    torch = pytest.importorskip("torch")
    pytest.importorskip("sionna.sys")
    from ottawa_rt.network_backend import UplinkBackend

    selected = scenario(
        direction="uplink",
        channel_source="paths",
        link_adaptation="fixed",
        fixed_mcs=4,
        downlink_fraction=0,
    )
    gains = np.asarray([[-90, -90, -90], [-90, -90, -90]])
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        backend = UplinkBackend(selected, np.asarray([0, 0, 1]), gains)
        _, rb, _, _ = backend.step(np.ones(3, dtype=bool))
        assert rb[:2].sum() == selected.num_rb and rb[2] == selected.num_rb
        assert np.all(backend.last_schedule.sum(-1) <= 1)  # orthogonal in cell
        interfered = backend.last_sinr_db[2]
        assert -0.1 < interfered < 0  # equal-power UE from the other cell
        assert rb.max() / selected.num_rb <= 1  # fixed PSD never exceeds UE power cap
        backend.step(np.asarray([False, False, True]))
        assert backend.last_sinr_db[2] > interfered + 20
        backend.step(np.zeros(3, dtype=bool))
        assert not backend.last_schedule.any()
        assert list(backend.last_feedback) == [-1, -1, -1]
    finally:
        torch.set_num_threads(old_threads)


def test_uplink_packet_direction_and_horizon_accounting(project, monkeypatch):
    pytest.importorskip("sionna.sys")

    def trace(source, ues):
        for ue in ues:
            ue.update(
                serving_sector_id="server",
                sinr_db=None,
                rsrp_dbm=-85,
                received_power_dbm=-67,
                operator="TEST",
                height_agl_m=1.5,
                elevation_m=81.5,
            )
        return {}, {
            "sector_ids": np.asarray(["server"]),
            "reciprocal_gain_db": np.full((1, len(ues)), -90),
        }

    monkeypatch.setattr("ottawa_rt.network_channels.trace_ue_channels", trace)
    selected = scenario(direction="uplink", channel_source="paths", downlink_fraction=0.75)
    result = run_network(project, selected)
    assert result["summary"]["uplink_slots"] == 10
    assert result["summary"]["downlink_slots"] == 0
    assert result["summary"]["total_throughput_mbps"] >= result["summary"]["total_goodput_mbps"]
    for ue in result["ues"]:
        assert ue["offered_packets"] == (
            ue["delivered_packets"] + ue["pending_packets"] + ue["dropped_packets"]
        )
        assert ue["scheduled_transport_blocks"] > 0
        assert ue["modulation_counts"]
        assert ue["mean_tx_power_mw"] <= 10 ** (selected.ue_tx_power_dbm / 10) * 0.25


@pytest.mark.parametrize("missing_value", [-9999.0, float("nan")])
def test_nodata_ue_reuses_pinned_rf_height_without_relocating(project, missing_value):
    from types import SimpleNamespace

    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin

    from ottawa_rt.data.provenance import sha256_file
    from ottawa_rt.network_channels import _ue_terrain_elevations

    terrain_path = project.paths.raw / "terrain" / "dtm.tif"
    terrain_path.parent.mkdir(parents=True)
    with rasterio.open(
        terrain_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(0, 2, 1, 1),
        nodata=-9999.0,
    ) as terrain:
        terrain.write(np.asarray([[91, missing_value], [93, 94]], dtype=np.float32), 1)
    run_dir = project.paths.runs / "rf"
    surface = run_dir / "surfaces/r000-c000.npz"
    surface.parent.mkdir()
    axis = np.arange(-9.5, 10, 1)
    np.savez_compressed(surface, x_m=axis, y_m=axis, receiver_z_m=np.full((20, 20), 3.5))
    source = SimpleNamespace(
        settings=project,
        metadata={"local_origin": {"elevation_m": 80}},
        manifest={"width_m": 20, "cell_size_m": 1},
        run_dir=run_dir,
        sources={},
    )
    ues = [
        {"ue_id": "valid", "longitude": 0.5, "latitude": 1.5, "x_m": -1.25, "y_m": 0.25},
        {"ue_id": "hole", "longitude": 1.5, "latitude": 1.5, "x_m": 1.25, "y_m": 0.25},
    ]
    coordinates = [(u["x_m"], u["y_m"]) for u in ues]
    elevations, fallbacks = _ue_terrain_elevations(source, ues)
    assert elevations == [91, 82]
    assert [(u["x_m"], u["y_m"]) for u in ues] == coordinates
    assert "terrain_nodata" not in ues[0]
    assert ues[1]["terrain_nodata"] is True
    assert fallbacks[0]["ue_id"] == "hole"
    assert fallbacks[0]["receiver_z_local_m"] == 3.5
    assert source.sources[project.portable_path(surface)] == sha256_file(surface)
    surface.unlink()
    with pytest.raises(ValueError, match="no height or placement substitution made"):
        _ue_terrain_elevations(source, ues)
    np.savez_compressed(surface, x_m=axis, y_m=axis, receiver_z_m=np.full((20, 20), np.nan))
    with pytest.raises(ValueError, match="height is invalid"):
        _ue_terrain_elevations(source, ues)
