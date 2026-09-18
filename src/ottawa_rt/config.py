from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    data: Path
    raw: Path
    processed: Path
    scenes: Path
    runs: Path
    measurements: Path
    calibration: Path

    @classmethod
    def from_config(cls, config_path: Path, data_root: str) -> ProjectPaths:
        root = config_path.resolve().parent.parent
        data = (root / data_root).resolve()
        return cls(
            root=root,
            data=data,
            raw=data / "raw",
            processed=data / "processed",
            scenes=data / "scenes",
            runs=data / "runs",
            measurements=data / "measurements",
            calibration=data / "calibration",
        )

    def ensure(self) -> None:
        for path in (
            self.data,
            self.raw,
            self.processed,
            self.scenes,
            self.runs,
            self.measurements,
            self.calibration,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    config_path: Path
    paths: ProjectPaths

    @property
    def anchor(self) -> tuple[float, float]:
        item = self.raw["anchor"]
        return float(item["latitude"]), float(item["longitude"])

    @property
    def cell_size_m(self) -> float:
        return float(self.raw["area"]["cell_size_m"])

    @property
    def default_receiver_height_m(self) -> float:
        return float(self.raw["receiver"]["default_height_agl_m"])

    def portable_path(self, path: Path) -> str:
        """Return a project-relative POSIX path suitable for WSL/DGX artifacts."""
        return path.resolve().relative_to(self.paths.root.resolve()).as_posix()

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.paths.root / path).resolve()


def load_settings(path: str | Path = "config/default.yaml") -> Settings:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise TypeError(f"Configuration must be a mapping: {config_path}")
    required = {"project", "paths", "anchor", "area", "compute", "sources"}
    missing = sorted(required.difference(raw))
    if missing:
        raise ValueError(f"Missing configuration sections: {', '.join(missing)}")
    paths = ProjectPaths.from_config(config_path, str(raw["paths"]["data_root"]))
    return Settings(raw=raw, config_path=config_path, paths=paths)
