from __future__ import annotations

from pathlib import Path

import pytest

from kindshot.backfill_auto import (
    AutoBackfillPlan,
    backfill_lock,
    build_auto_backfill_report,
    build_auto_backfill_round_report,
    compute_auto_backfill_plan,
    default_lock_path,
    format_auto_noop_message,
    write_auto_backfill_report,
)
from kindshot.collector import BackfillResult, CollectorState, save_collector_state
from kindshot.config import Config


def _cfg(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
    )


def _result() -> BackfillResult:
    return BackfillResult(
        requested_from="20260313",
        requested_to="20260312",
        finalized_date="20260315",
        processed_dates=["20260313", "20260312"],
        completed_dates=["20260313"],
        partial_dates=["20260312"],
        news_counts={"20260313": 3, "20260312": 1},
        classification_counts={"20260313": 3, "20260312": 1},
        price_counts={"20260313": 2, "20260312": 0},
        index_counts={"20260313": 2, "20260312": 0},
        skipped_dates=["20260311"],
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


def test_build_auto_backfill_round_report_copies_result_details(tmp_path):
    plan = AutoBackfillPlan(
        finalized_date="20260315",
        requested_from="20260313",
        requested_to="20260312",
        max_days=2,
        cursor_date="20260313",
        oldest_date="20260301",
    )

    report = build_auto_backfill_round_report(1, plan, _result())

    assert report["round"] == 1
    assert report["requested_from"] == plan.requested_from
    assert report["requested_to"] == plan.requested_to
    assert report["processed_dates"] == ["20260313", "20260312"]
    assert report["partial_dates"] == ["20260312"]
    assert report["price_counts"]["20260312"] == 0


def test_build_auto_backfill_report_includes_policy_and_stop_reason(tmp_path):
    cfg = _cfg(tmp_path)
    state = CollectorState(
        cursor_date="20260312",
        last_completed_date="20260313",
        finalized_date="20260315",
        status="idle",
        updated_at="2026-03-27T09:00:00+09:00",
    )
    round_row = build_auto_backfill_round_report(
        1,
        AutoBackfillPlan(
            finalized_date="20260315",
            requested_from="20260313",
            requested_to="20260312",
            max_days=2,
            cursor_date="20260313",
            oldest_date="20260301",
        ),
        _result(),
    )

    report = build_auto_backfill_report(
        max_days=2,
        max_rounds=5,
        stop_hour=7,
        oldest_date="20260301",
        notify_noop=True,
        stop_reason="caught_up",
        rounds=[round_row],
        state=state,
        status_report={"summary": {"health": "partial_backlog"}},
        latest_backfill_report_path=str(cfg.collector_backfill_report_path),
    )

    assert report["request"]["max_days"] == 2
    assert report["request"]["notify_noop"] is True
    assert report["result"]["status"] == "success"
    assert report["result"]["stop_reason"] == "caught_up"
    assert report["result"]["round_count"] == 1
    assert report["result"]["latest_backfill_report_path"] == str(cfg.collector_backfill_report_path)
    assert report["collector_status"]["summary"]["health"] == "partial_backlog"
    assert report["collector_state"]["cursor_date"] == "20260312"


def test_write_auto_backfill_report_writes_default_path(tmp_path):
    cfg = _cfg(tmp_path)
    state = CollectorState(cursor_date="20260312", finalized_date="20260315", status="idle")

    report, path = write_auto_backfill_report(
        cfg,
        max_days=2,
        max_rounds=5,
        stop_hour=7,
        oldest_date="20260301",
        notify_noop=False,
        stop_reason="backfill_floor_reached",
        rounds=[],
        state=state,
        status_report={"summary": {"health": "ok"}},
    )

    assert path == cfg.collector_backfill_auto_report_path
    assert report["result"]["status"] == "noop"
    payload = path.read_text(encoding="utf-8")
    assert '"source": "collect_backfill_auto"' in payload
    assert '"stop_reason": "backfill_floor_reached"' in payload
