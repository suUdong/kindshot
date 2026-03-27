"""Tests for performance tracking module."""

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kindshot.performance import PerformanceTracker, TradeRecord, DailySummary
from kindshot.tz import KST as _KST


def test_record_trade_returns_record(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    rec = tracker.record_trade("005930", 50000, 50500, 1.0, size_won=5_000_000)
    assert isinstance(rec, TradeRecord)
    assert rec.ticker == "005930"
    assert rec.pnl_pct == 1.0
    assert rec.pnl_won == 50000.0  # 5M * 1%


def test_daily_summary_empty(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    s = tracker.daily_summary()
    assert isinstance(s, DailySummary)
    assert s.total_trades == 0
    assert s.win_rate == 0.0


def test_daily_summary_with_trades(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    tracker.record_trade("005930", 50000, 50500, 1.0, size_won=5_000_000)
    tracker.record_trade("035420", 30000, 29700, -1.0, size_won=5_000_000)
    tracker.record_trade("000660", 80000, 80800, 1.0, size_won=5_000_000)

    s = tracker.daily_summary()
    assert s.total_trades == 3
    assert s.wins == 2
    assert s.losses == 1
    assert abs(s.win_rate - 66.67) < 1.0
    assert s.total_pnl_pct == 1.0  # +1 -1 +1
    assert s.avg_win_pct == 1.0
    assert s.avg_loss_pct == -1.0
    assert s.profit_factor == 2.0


def test_flush_creates_summary_file(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    tracker.record_trade("005930", 50000, 50500, 1.0)
    path = tracker.flush()
    assert path is not None
    assert path.exists()
    assert "_summary.json" in path.name


def test_flush_empty_returns_none(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    assert tracker.flush() is None


def test_trade_log_jsonl_created(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    tracker.record_trade("005930", 50000, 50500, 1.0)
    jsonl_files = list((tmp_path / "performance").glob("*_trades.jsonl"))
    assert len(jsonl_files) == 1
    content = jsonl_files[0].read_text()
    assert "005930" in content


def test_profit_factor_no_losses(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    tracker.record_trade("005930", 50000, 50500, 1.0)
    s = tracker.daily_summary()
    assert s.profit_factor == float("inf")


def test_exit_type_recorded(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    rec = tracker.record_trade("005930", 50000, 50500, 1.0, exit_type="TP", confidence=85)
    assert rec.exit_type == "TP"
    assert rec.confidence == 85


def test_daily_summary_rolls_date_forward_without_trade(tmp_path):
    tracker = PerformanceTracker(tmp_path)
    tracker._current_date = "2026-03-27"
    with patch("kindshot.performance.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 28, 10, 0, tzinfo=_KST)
        summary = tracker.daily_summary()
    assert summary.date == "2026-03-28"
