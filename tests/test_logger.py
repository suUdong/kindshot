"""Tests for JSONL logger."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from kindshot.logger import JsonlLogger, LogWriteError
from kindshot.models import EventRecord, Bucket, EventIdMethod
from kindshot.tz import KST as _KST


def _make_event(event_id: str = "test123") -> EventRecord:
    return EventRecord(
        schema_version="0.1.2",
        run_id="run_test",
        event_id=event_id,
        event_id_method=EventIdMethod.UID,
        event_group_id=event_id,
        detected_at=datetime.now(timezone.utc),
        ticker="005930",
        corp_name="삼성전자",
        headline="테스트 공시",
        bucket=Bucket.POS_STRONG,
    )


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


async def test_write_and_read_back(log_dir: Path):
    logger = JsonlLogger(log_dir, run_id="run_test")
    event = _make_event()
    await logger.write(event)

    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1

    line = files[0].read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["type"] == "event"
    assert data["event_id"] == "test123"
    assert data["ticker"] == "005930"


async def test_multiple_writes_append(log_dir: Path):
    logger = JsonlLogger(log_dir, run_id="run_test")
    await logger.write(_make_event("a"))
    await logger.write(_make_event("b"))

    files = list(log_dir.glob("*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


async def test_daily_rotation(log_dir: Path):
    logger = JsonlLogger(log_dir, run_id="run_test")
    await logger.write(_make_event())
    # File name should contain today's date
    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    today_kst = datetime.now(_KST).strftime("%Y%m%d")
    assert today_kst in files[0].name


async def test_log_write_error_on_readonly_dir(tmp_path: Path):
    """LogWriteError raised when write fails (e.g., permission denied)."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    logger = JsonlLogger(log_dir, run_id="run_test")

    # Make the log dir read-only to force OSError
    log_dir.chmod(0o444)
    try:
        with pytest.raises(LogWriteError):
            await logger.write(_make_event())
    finally:
        log_dir.chmod(0o755)


async def test_file_uses_kst_date(log_dir: Path):
    """Log file name should use KST date, not UTC."""
    logger = JsonlLogger(log_dir, run_id="run_test")
    await logger.write(_make_event())

    files = list(log_dir.glob("*.jsonl"))
    assert len(files) == 1
    kst_date = datetime.now(_KST).strftime("%Y%m%d")
    assert kst_date in files[0].name
