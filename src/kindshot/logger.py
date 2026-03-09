"""JSONL append-only logger with asyncio safety."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class LogWriteError(Exception):
    """Raised when log writing fails — triggers fail-stop."""


class JsonlLogger:
    """Append-only JSONL logger. Thread-safe via asyncio.Lock + to_thread."""

    def __init__(self, log_dir: Path, run_id: str, file_prefix: str = "kindshot") -> None:
        self._log_dir = log_dir
        self._run_id = run_id
        self._file_prefix = file_prefix
        self._lock = asyncio.Lock()
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _today_file(self) -> Path:
        today = datetime.now().strftime("%Y%m%d")
        return self._log_dir / f"{self._file_prefix}_{today}.jsonl"

    def _write_sync(self, line: str) -> None:
        path = self._today_file()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def write(self, record: BaseModel) -> None:
        line = record.model_dump_json(exclude_none=False)
        try:
            async with self._lock:
                await asyncio.to_thread(self._write_sync, line)
        except OSError as e:
            raise LogWriteError(f"Log write failed: {e}") from e
