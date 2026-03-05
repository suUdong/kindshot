"""Tests for JSONL logger."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kindshot.logger import JsonlLogger
from kindshot.models import EventRecord, Bucket, EventIdMethod


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
    today = datetime.now().strftime("%Y%m%d")
    assert today in files[0].name
