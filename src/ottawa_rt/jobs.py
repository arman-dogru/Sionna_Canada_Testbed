from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class SimulationJob:
    job_id: str
    run_id: str
    scene_xml: str
    tile: dict[str, object]
    frequency_mhz: float
    sector_ids: list[str]
    output_path: str
    measurement_surface: str | None = None


class FileJobQueue:
    """Atomic, resumable filesystem queue suitable for local and shared DGX storage."""

    states = ("pending", "processing", "done", "failed")

    def __init__(self, root: Path):
        self.root = root
        for state in self.states:
            (root / state).mkdir(parents=True, exist_ok=True)

    def enqueue(self, job: SimulationJob) -> Path:
        path = self.root / "pending" / f"{job.job_id}.json"
        for state in self.states[1:]:
            existing = self.root / state / path.name
            if existing.exists():
                return existing
        if not path.exists():
            path.write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")
        return path

    def retry_failed(self) -> int:
        retried = 0
        for failed in sorted((self.root / "failed").glob("*.json")):
            payload = json.loads(failed.read_text(encoding="utf-8"))
            for key in ("claimed_by", "claimed_at", "finished_at", "metrics", "error"):
                payload.pop(key, None)
            destination = self.root / "pending" / failed.name
            failed.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._replace_with_retry(failed, destination)
            retried += 1
        return retried

    def claim(self) -> tuple[SimulationJob, Path] | None:
        for pending in sorted((self.root / "pending").glob("*.json")):
            claimed = self.root / "processing" / pending.name
            try:
                pending.replace(claimed)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            payload = json.loads(claimed.read_text(encoding="utf-8"))
            payload["claimed_by"] = f"{socket.gethostname()}:{os.getpid()}"
            payload["claimed_at"] = datetime.now(UTC).isoformat()
            claimed.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            allowed = {field.name for field in SimulationJob.__dataclass_fields__.values()}
            return SimulationJob(
                **{key: value for key, value in payload.items() if key in allowed}
            ), claimed
        return None

    def finish(
        self, claimed: Path, *, error: str | None = None, metrics: dict[str, object] | None = None
    ) -> Path:
        payload = json.loads(claimed.read_text(encoding="utf-8"))
        payload["finished_at"] = datetime.now(UTC).isoformat()
        payload["metrics"] = metrics or {}
        if error:
            payload["error"] = error
        state = "failed" if error else "done"
        destination = self.root / state / claimed.name
        claimed.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._replace_with_retry(claimed, destination)
        return destination

    @staticmethod
    def _replace_with_retry(source: Path, destination: Path, attempts: int = 12) -> None:
        """Move a queue record despite short-lived Windows scanner/indexer locks."""
        for attempt in range(attempts):
            try:
                source.replace(destination)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(min(0.05 * (2**attempt), 1.0))

    def counts(self) -> dict[str, int]:
        return {state: len(list((self.root / state).glob("*.json"))) for state in self.states}

    def wait_for_work(self, seconds: float = 2.0) -> None:
        time.sleep(seconds)
