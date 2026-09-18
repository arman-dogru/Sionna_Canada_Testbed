from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data" / "runs" / "00-smoke-2000m"
TILE = RUN / "tiles" / "r000-c000-1940p0MHz.npz"
SURFACE = RUN / "surfaces" / "r000-c000.npz"
JOB = RUN / "queue" / "done" / "r000-c000-1940p0MHz.json"
OUTPUT = ROOT / "screenshots" / "processed-smoke-1940mhz.png"


def selected_values(values: np.ndarray, selected: np.ndarray, valid: np.ndarray) -> np.ndarray:
    result = np.take_along_axis(values, selected[None, ...], axis=0)[0].astype(float)
    result[~valid] = np.nan
    return result


def main() -> None:
    tile = np.load(TILE)
    surface = np.load(SURFACE)
    job = json.loads(JOB.read_text(encoding="utf-8"))

    rsrp = tile["rsrp_dbm"]
    valid = np.isfinite(rsrp).any(axis=0)
    selected = np.argmax(np.where(np.isfinite(rsrp), rsrp, -np.inf), axis=0)
    strongest_rsrp = selected_values(rsrp, selected, valid)
    path_gain = selected_values(tile["path_gain_db"], selected, valid)
    sinr = selected_values(tile["sinr_db"], selected, valid)
    hit_count = np.isfinite(rsrp).sum(axis=0).astype(float)
    hit_count[~valid] = np.nan

    x = surface["x_m"]
    y = surface["y_m"]
    terrain = surface["receiver_z_m"]
    x_grid, y_grid = np.meshgrid(x, y)
    hit_x, hit_y = x_grid[valid], y_grid[valid]
    extent = [float(x[0] - 2.5), float(x[-1] + 2.5), float(y[0] - 2.5), float(y[-1] + 2.5)]

    plt.style.use("dark_background")
    figure, axes = plt.subplots(2, 2, figsize=(15, 12), constrained_layout=False)
    figure.patch.set_facecolor("#07111f")
    figure.subplots_adjust(left=0.08, right=0.94, top=0.87, bottom=0.13, wspace=0.23, hspace=0.32)
    metrics = [
        (hit_count, "Ray-hit sectors per receiver cell", "viridis", None, "sector hits"),
        (strongest_rsrp, "Strongest estimated RSRP", "turbo", (-120, -20), "dBm"),
        (path_gain, "Path gain for strongest sector", "magma", (-100, -50), "dB"),
        (sinr, "SINR for strongest sector", "coolwarm", (-30, 40), "dB"),
    ]
    for axis, (values, title, cmap, limits, unit) in zip(axes.flat, metrics, strict=True):
        axis.set_facecolor("#07111f")
        axis.imshow(
            terrain,
            origin="lower",
            extent=extent,
            cmap="Greys",
            alpha=0.27,
            interpolation="nearest",
        )
        colors = values[valid]
        scatter_options: dict[str, object] = {
            "c": colors,
            "cmap": cmap,
            "marker": "s",
            "s": 62,
            "edgecolors": "white",
            "linewidths": 0.35,
        }
        if limits is not None:
            scatter_options.update(vmin=limits[0], vmax=limits[1])
        else:
            maximum = max(int(np.nanmax(colors)), 1)
            scatter_options["norm"] = BoundaryNorm(np.arange(0.5, maximum + 1.5), maximum)
        plotted = axis.scatter(hit_x, hit_y, **scatter_options)
        axis.add_patch(
            Rectangle((-1000, -1000), 1000, 1000, fill=False, edgecolor="#38bdf8", linewidth=1.2)
        )
        axis.scatter([0], [0], marker="*", s=120, c="#fb7185", edgecolors="white", linewidths=0.8)
        axis.set_title(title, loc="left", fontsize=13, fontweight="bold", pad=10)
        axis.set_xlabel("Local easting from 350 Legget Drive (m)")
        axis.set_ylabel("Local northing from 350 Legget Drive (m)")
        axis.set_xlim(extent[0], extent[1])
        axis.set_ylim(extent[2], extent[3])
        axis.set_aspect("equal")
        axis.grid(color="white", alpha=0.08, linewidth=0.5)
        colorbar = figure.colorbar(plotted, ax=axis, shrink=0.83, pad=0.02)
        colorbar.set_label(unit)
        if limits is None:
            colorbar.set_ticks(range(1, maximum + 1))

    metrics = job["metrics"]
    spatial_hits = int(valid.sum())
    total_cells = int(valid.size)
    figure.suptitle(
        "Actual processed Sionna RT smoke tile — 1940 MHz\n"
        f"r000-c000 · {metrics['transmitter_count']} transmitters · "
        f"{metrics['samples_per_tx']:,} samples/transmitter · depth {metrics['max_depth']} · "
        f"{metrics['elapsed_seconds']:.3f} s",
        fontsize=17,
        fontweight="bold",
        color="white",
        y=0.965,
    )
    figure.text(
        0.5,
        0.025,
        f"Only {spatial_hits:,} of {total_cells:,} receiver cells ({100 * spatial_hits / total_cells:.3f}%) "
        "received any finite ray contribution; transparent cells had no hit.\n"
        "Blue outline = 1 km output core; outer 50 m = overlap; star = Legget Drive anchor. "
        "Low-sample smoke test—not a production coverage map.",
        ha="center",
        va="bottom",
        fontsize=10,
        color="#cbd5e1",
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(OUTPUT, dpi=180, facecolor=figure.get_facecolor(), bbox_inches="tight")
    plt.close(figure)
    print(OUTPUT)


if __name__ == "__main__":
    main()
