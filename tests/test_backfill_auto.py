from __future__ import annotations

from pathlib import Path

import pytest

from kindshot.backfill_auto import backfill_lock, compute_auto_backfill_plan, default_lock_path, format_auto_noop_message
from kindshot.collector import CollectorState, save_collector_state
from kindshot.config import Config


def _cfg(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
    )


def test_compute_auto_backfill_plan_uses_cursor_and_max_days(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_collector_state(cfg.collector_state_path, CollectorState(cursor_date="20260313"))
    monkeypatch.setattr("kindshot.backfill_auto.compute_finalized_date", lambda **_: "20260315")

    plan = compute_auto_backfill_plan(cfg, max_days=4)

    assert plan is not None
    assert plan.finalized_date == "20260315"
    assert plan.requested_from == "20260313"
    assert plan.requested_to == "20260310"


def test_compute_auto_backfill_plan_prefers_latest_partial_backlog(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_collector_state(cfg.collector_state_path, CollectorState(cursor_date="20260307"))
    cfg.collector_log_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.collector_log_path.write_text(
        "\n".join(
            [
                '{"date":"20260310","status":"partial","news_count":0,"classification_count":0,"daily_price_count":0,"daily_index_count":0,"completed_at":"2026-03-16T20:44:43+09:00","error":"","skip_reason":"daily_index_missing"}',
                '{"date":"20260309","status":"partial","news_count":0,"classification_count":0,"daily_price_count":0,"daily_index_count":0,"completed_at":"2026-03-16T20:44:44+09:00","error":"","skip_reason":"daily_index_missing"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("kindshot.backfill_auto.compute_finalized_date", lambda **_: "20260315")

    plan = compute_auto_backfill_plan(cfg, max_days=2, oldest_date="20260301")

    assert plan is not None
    assert plan.requested_from == "20260310"
    assert plan.requested_to == "20260309"


def test_compute_auto_backfill_plan_clamps_to_oldest_date(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_collector_state(cfg.collector_state_path, CollectorState(cursor_date="20260313"))
    monkeypatch.setattr("kindshot.backfill_auto.compute_finalized_date", lambda **_: "20260315")

    plan = compute_auto_backfill_plan(cfg, max_days=10, oldest_date="20260311")

    assert plan is not None
    assert plan.requested_from == "20260313"
    assert plan.requested_to == "20260311"


def test_compute_auto_backfill_plan_returns_none_when_floor_already_passed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    save_collector_state(cfg.collector_state_path, CollectorState(cursor_date="20260228"))
    monkeypatch.setattr("kindshot.backfill_auto.compute_finalized_date", lambda **_: "20260315")

    plan = compute_auto_backfill_plan(cfg, max_days=4, oldest_date="20260301")

    assert plan is None


def test_format_auto_noop_message_is_concise():
    text = format_auto_noop_message(None, cursor_date="20260228", oldest_date="20260301", finalized_date="20260315")
    assert "Kindshot Backfill AUTO NOOP" in text
    assert "reason=backfill_floor_reached" in text
    assert "cursor=20260228 oldest=20260301" in text


def test_backfill_lock_rejects_overlap(tmp_path):
    cfg = _cfg(tmp_path)
    lock_path = default_lock_path(cfg)
    with backfill_lock(lock_path):
        with pytest.raises(FileExistsError):
            with backfill_lock(lock_path):
                pass
    assert not lock_path.exists()
