"""Helpers for runtime artifact discovery metadata."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from kindshot.config import Config


def _empty_runtime_index() -> dict:
    return {"generated_at": "", "entries": []}


async def update_runtime_artifact_index(
    config: Config,
    *,
    date: str,
    artifact: str,
    path: Path,
    recorded_at: datetime,
) -> None:
    """Upsert runtime artifact metadata for a specific KST date."""

    index_path = config.runtime_index_path
    if index_path.exists():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        payload = _empty_runtime_index()

    entries = payload.setdefault("entries", [])
    row = next((entry for entry in entries if entry.get("date") == date), None)
    if row is None:
        row = {"date": date, "generated_at": "", "artifacts": {}}
        entries.append(row)

    artifacts = row.setdefault("artifacts", {})
    artifacts[artifact] = {
        "path": str(path),
        "exists": path.exists(),
        "recorded_at": recorded_at.isoformat(),
    }
    row["generated_at"] = recorded_at.isoformat()
    entries.sort(key=lambda entry: str(entry.get("date", "")), reverse=True)
    payload["generated_at"] = recorded_at.isoformat()

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
