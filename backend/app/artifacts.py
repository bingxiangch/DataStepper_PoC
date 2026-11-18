"""Immutable Parquet artifacts and append-only run events."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from .config import RUNS_DIR
from .models import utcnow


class ArtifactStore:
    """Content-addressed-by-ID immutable table artifacts.

    Every produced table is written once.  Runs only reference IDs, so a
    checkpoint is a small mapping from logical table name to artifact ID.
    """

    def __init__(self, root: Path = RUNS_DIR / "artifacts"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._frames: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def put_frame(self, df: pd.DataFrame, run_id: str, label: str) -> str:
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        path = self.root / f"{artifact_id}.parquet"
        df.to_parquet(path, index=False)
        meta = {
            "id": artifact_id,
            "run_id": run_id,
            "label": label,
            "rows": int(len(df)),
            "columns": list(map(str, df.columns)),
            "created_at": utcnow(),
        }
        (self.root / f"{artifact_id}.json").write_text(json.dumps(meta, indent=2))
        with self._lock:
            self._frames[artifact_id] = df
        return artifact_id

    def get_frame(self, artifact_id: str, dataset) -> pd.DataFrame:
        if artifact_id.startswith("src:"):
            return dataset.table(artifact_id.split(":", 1)[1])
        with self._lock:
            cached = self._frames.get(artifact_id)
        if cached is not None:
            return cached
        path = self.root / f"{artifact_id}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Artifact {artifact_id!r} is referenced but missing at {path}.")
        df = pd.read_parquet(path)
        with self._lock:
            self._frames[artifact_id] = df
        return df

    def meta(self, artifact_id: str) -> dict[str, Any]:
        if artifact_id.startswith("src:"):
            return {"id": artifact_id, "kind": "source_table", "table": artifact_id.split(":", 1)[1]}
        path = self.root / f"{artifact_id}.json"
        if path.exists():
            return json.loads(path.read_text())
        return {"id": artifact_id}


class EventLog:
    """One JSONL file per run for execution provenance."""

    def __init__(self, root: Path = RUNS_DIR):
        self.root = Path(root)

    def append(self, run_id: str, event_type: str, message: str, **fields: Any) -> int:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "events.jsonl"
        record = {
            "ts": utcnow(),
            "type": event_type,
            "message": message,
            **fields,
        }
        with _EVENT_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        return self.count(run_id)

    def count(self, run_id: str) -> int:
        path = self.root / run_id / "events.jsonl"
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as fh:
            return sum(1 for _ in fh)

    def tail(self, run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        path = self.root / run_id / "events.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()]


_EVENT_LOCK = threading.Lock()


class RunRepository:
    def __init__(self, root: Path = RUNS_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, run) -> None:
        run_dir = self.root / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = run.model_dump(mode="json")
        tmp = run_dir / "run.json.tmp"
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(run_dir / "run.json")

    def load_all(self) -> list:
        from .models import Run

        runs = []
        for path in sorted(self.root.glob("run_*/run.json")):
            try:
                runs.append(Run.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return runs
