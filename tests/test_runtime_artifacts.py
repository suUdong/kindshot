"""Tests for runtime_artifacts.py — runtime index upsert logic."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kindshot.config import Config
from kindshot.runtime_artifacts import update_runtime_artifact_index


@pytest.fixture
def config_with_tmp(tmp_path):
    return Config(runtime_index_path=tmp_path / "runtime_index.json")


@pytest.mark.asyncio
async def test_creates_index_if_missing(config_with_tmp):
    cfg = config_with_tmp
    await update_runtime_artifact_index(
        cfg,
        date="20260316",
        artifact="context_cards",
        path=Path("/tmp/ctx.jsonl"),
        recorded_at=datetime.now(timezone.utc),
    )
    assert cfg.runtime_index_path.exists()
    data = json.loads(cfg.runtime_index_path.read_text())
    assert len(data["entries"]) == 1
    assert data["entries"][0]["date"] == "20260316"
    assert "context_cards" in data["entries"][0]["artifacts"]


@pytest.mark.asyncio
async def test_upserts_same_date(config_with_tmp):
    cfg = config_with_tmp
    now = datetime.now(timezone.utc)
    await update_runtime_artifact_index(cfg, date="20260316", artifact="context_cards", path=Path("/tmp/a.jsonl"), recorded_at=now)
    await update_runtime_artifact_index(cfg, date="20260316", artifact="price_snapshots", path=Path("/tmp/b.jsonl"), recorded_at=now)

    data = json.loads(cfg.runtime_index_path.read_text())
    assert len(data["entries"]) == 1
    artifacts = data["entries"][0]["artifacts"]
    assert "context_cards" in artifacts
    assert "price_snapshots" in artifacts


@pytest.mark.asyncio
async def test_different_dates_create_separate_entries(config_with_tmp):
    cfg = config_with_tmp
    now = datetime.now(timezone.utc)
    await update_runtime_artifact_index(cfg, date="20260316", artifact="context_cards", path=Path("/tmp/a.jsonl"), recorded_at=now)
    await update_runtime_artifact_index(cfg, date="20260317", artifact="context_cards", path=Path("/tmp/b.jsonl"), recorded_at=now)

    data = json.loads(cfg.runtime_index_path.read_text())
    assert len(data["entries"]) == 2
    dates = {e["date"] for e in data["entries"]}
    assert dates == {"20260316", "20260317"}
