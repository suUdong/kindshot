"""종목별 학습 모듈 테스트."""
import json
import pytest
from pathlib import Path
from kindshot.ticker_learning import TickerLearner, TickerStats


def _write_trades(tmp_path: Path, trades: list[dict]) -> Path:
    """테스트용 trades JSONL 작성."""
    perf_dir = tmp_path / "performance"
    perf_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = perf_dir / "2026-03-27_trades.jsonl"
    lines = [json.dumps(t, ensure_ascii=False) for t in trades]
    jsonl_path.write_text("\n".join(lines), encoding="utf-8")
    return tmp_path


def test_load_history_empty(tmp_path):
    learner = TickerLearner()
    assert learner.load_history(tmp_path) == 0
    assert learner.ticker_count == 0


def test_load_history_with_trades(tmp_path):
    trades = [
        {"ticker": "005930", "pnl_pct": 1.5},
        {"ticker": "005930", "pnl_pct": -0.5},
        {"ticker": "005930", "pnl_pct": 2.0},
        {"ticker": "000660", "pnl_pct": -1.0},
    ]
    data_dir = _write_trades(tmp_path, trades)
    learner = TickerLearner()
    assert learner.load_history(data_dir) == 4
    assert learner.ticker_count == 2


def test_get_adjustment_high_win_rate(tmp_path):
    trades = [
        {"ticker": "005930", "pnl_pct": 1.5},
        {"ticker": "005930", "pnl_pct": 2.0},
        {"ticker": "005930", "pnl_pct": 1.0},
        {"ticker": "005930", "pnl_pct": -0.5},
    ]
    data_dir = _write_trades(tmp_path, trades)
    learner = TickerLearner(min_trades=3)
    learner.load_history(data_dir)
    assert learner.get_adjustment("005930") == 5  # 75% 승률 → +5 (>= 70%)


def test_get_adjustment_low_win_rate(tmp_path):
    trades = [
        {"ticker": "005930", "pnl_pct": -1.0},
        {"ticker": "005930", "pnl_pct": -0.5},
        {"ticker": "005930", "pnl_pct": -2.0},
        {"ticker": "005930", "pnl_pct": 0.5},
    ]
    data_dir = _write_trades(tmp_path, trades)
    learner = TickerLearner(min_trades=3)
    learner.load_history(data_dir)
    assert learner.get_adjustment("005930") == -3  # 25% 승률 → -3


def test_get_adjustment_insufficient_data(tmp_path):
    trades = [
        {"ticker": "005930", "pnl_pct": 1.5},
        {"ticker": "005930", "pnl_pct": 2.0},
    ]
    data_dir = _write_trades(tmp_path, trades)
    learner = TickerLearner(min_trades=3)
    learner.load_history(data_dir)
    assert learner.get_adjustment("005930") == 0  # 2거래 < min_trades(3)


def test_get_adjustment_unknown_ticker():
    learner = TickerLearner()
    assert learner.get_adjustment("999999") == 0


def test_get_stats(tmp_path):
    trades = [
        {"ticker": "005930", "pnl_pct": 1.5},
        {"ticker": "005930", "pnl_pct": -0.5},
        {"ticker": "005930", "pnl_pct": 2.0},
    ]
    data_dir = _write_trades(tmp_path, trades)
    learner = TickerLearner()
    learner.load_history(data_dir)
    stats = learner.get_stats("005930")
    assert stats is not None
    assert stats.total_trades == 3
    assert stats.wins == 2
    assert stats.losses == 1
    assert abs(stats.win_rate - 0.6667) < 0.01
