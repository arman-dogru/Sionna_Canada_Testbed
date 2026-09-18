from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ottawa_rt.models import ProvenanceEntry


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class ProvenanceManifest:
    def __init__(self, path: Path, project_root: Path | None = None):
        self.path = path
        self.project_root = (project_root or path.parent.parent.parent).resolve()

    def resolve_local_path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.project_root / path).resolve()

    def _portable_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()

    def read(self) -> list[ProvenanceEntry]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return [ProvenanceEntry.model_validate(item) for item in payload.get("entries", [])]

    def add_file(
        self,
        *,
        name: str,
        source_url: str,
        path: Path,
        licence: str,
        metadata: dict[str, object] | None = None,
    ) -> ProvenanceEntry:
        entry = ProvenanceEntry(
            name=name,
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
            local_path=self._portable_path(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            licence=licence,
            metadata=metadata or {},
        )
        existing = [item for item in self.read() if item.name != name]
        existing.append(entry)
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "entries": [
                item.model_dump(mode="json") for item in sorted(existing, key=lambda x: x.name)
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return entry

    def make_portable(self) -> None:
        """Migrate manifests made by older builds away from host-specific paths."""
        entries = self.read()
        changed = False
        for index, entry in enumerate(entries):
            portable = self._portable_path(self.resolve_local_path(entry.local_path))
            if portable != entry.local_path:
                entries[index] = entry.model_copy(update={"local_path": portable})
                changed = True
        if not changed:
            return
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "entries": [
                item.model_dump(mode="json") for item in sorted(entries, key=lambda x: x.name)
            ],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)
