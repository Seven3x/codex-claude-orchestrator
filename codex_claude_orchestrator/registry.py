from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import JobRecord
from .util import ensure_dir


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = ensure_dir(root)
        self.path = self.root / "registry.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def upsert(self, record: JobRecord) -> None:
        data = self._load()
        data[record.job_id] = record.to_dict()
        self._save(data)

    def get(self, job_id: str) -> JobRecord | None:
        data = self._load()
        rec = data.get(job_id)
        if not rec:
            return None
        return JobRecord.from_dict(rec)

    def all(self) -> dict[str, JobRecord]:
        raw = self._load()
        return {k: JobRecord.from_dict(v) for k, v in raw.items()}
