from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ottawa_rt.config import Settings

PANELS = (
    ("max_path_gain_db", "Highest path gain across all TXs", "Path gain (dB)", (-190.0, -70.0)),
    (
        "max_rss_dbm",
        "Highest RSS across all TXs",
        "Received signal strength (dBm)",
        (-145.0, -35.0),
    ),
    (
        "max_sinr_db",
        "Highest SINR across all TXs",
        "Signal-to-interference-plus-noise ratio (dB)",
        (-20.0, 70.0),
    ),
)


def _frequency_name(path: Path) -> str:
    return "All bands" if path.stem == "all-bands" else path.stem.replace("MHz", " MHz")


def render_run(
    settings: Settings,
    run_id: str,
    *,
    frequency_mhz: float | None = None,
) -> dict[str, object]:
    """Render reference-style engineering summaries from stitched Sionna outputs."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    run_dir = settings.paths.runs / run_id
    stitched_dir = run_dir / "stitched"
    if frequency_mhz is None:
        sources = sorted(stitched_dir.glob("*MHz.npz"))
        aggregate = stitched_dir / "all-bands.npz"
        if aggregate.exists():
            sources.append(aggregate)
    else:
        sources = [stitched_dir / f"{frequency_mhz:.1f}MHz.npz"]
    sources = [path for path in sources if path.exists()]
    if not sources:
        raise FileNotFoundError(f"No stitched coverage is available for run {run_id}")

    output_dir = run_dir / "visualization"
    output_dir.mkdir(exist_ok=True)
    outputs = []
    for source in sources:
        with np.load(source, allow_pickle=False) as data:
            layers = {field: np.asarray(data[field], dtype=np.float32) for field, *_ in PANELS}
            cell_size = float(data["cell_size_m"])
            width_m = float(data["width_m"])
            bounds = np.asarray(data["bounds_wgs84"], dtype=float).tolist()

        figure, axes = plt.subplots(2, 2, figsize=(13.5, 10.0), constrained_layout=True)
        axes[1, 1].axis("off")
        for panel_index, (field, title, colorbar_title, limits) in enumerate(PANELS):
            axis = axes.flat[panel_index]
            values = np.ma.masked_invalid(layers[field])
            values = np.ma.masked_where(~np.isfinite(values) | (values <= -199.0), values)
            colormap = plt.get_cmap("viridis").copy()
            colormap.set_bad("white")
            image = axis.imshow(
                values,
                origin="lower",
                cmap=colormap,
                vmin=limits[0],
                vmax=limits[1],
                interpolation="nearest",
            )
            axis.set_title(title)
            axis.set_xlabel(f"Cell index (X-axis, {cell_size:g} m)")
            axis.set_ylabel(f"Cell index (Y-axis, {cell_size:g} m)")
            colorbar = figure.colorbar(image, ax=axis, shrink=0.88)
            colorbar.set_label(colorbar_title)
            axis.text(
                0.02,
                0.02,
                f"Finite cells: {int(np.isfinite(layers[field]).sum()):,}",
                transform=axis.transAxes,
                fontsize=8,
                color="white",
                bbox={"facecolor": "black", "alpha": 0.55, "pad": 3, "edgecolor": "none"},
            )
        figure.suptitle(
            f"Ottawa Sionna RT · {_frequency_name(source)} · {width_m / 1000:g} km coverage",
            fontsize=15,
        )
        output = output_dir / f"{source.stem}-summary.png"
        figure.savefig(output, dpi=180, facecolor="white")
        plt.close(figure)
        outputs.append(
            {
                "source": settings.portable_path(source),
                "image": settings.portable_path(output),
                "bounds_wgs84": bounds,
                "cell_size_m": cell_size,
                "panels": [field for field, *_ in PANELS],
            }
        )

    report = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "outputs": outputs,
    }
    (output_dir / "visualization-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report
