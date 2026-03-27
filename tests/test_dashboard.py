"""Dashboard data_loader 단위 테스트."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# dashboard/ 를 import path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

from data_loader import (  # noqa: E402
    _read_jsonl,
    _read_json,
    available_dates,
    compute_daily_equity_curve,
    compute_multi_day_pnl,
    compute_trade_pnl,
    load_context_cards,
    load_events,
    load_health,
    load_live_feed,
    load_multi_day_events,
    load_multi_day_pnl_detail,
    load_price_snapshots,
    load_shadow_trade_pnl,
    load_version_trend,
    summarize_shadow_trade_pnl,
)


@pytest.fixture
def fake_logs(tmp_path, monkeypatch):
    """JSONL 로그 파일을 임시 디렉토리에 생성."""
    import data_loader

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    runtime_dir = tmp_path / "data" / "runtime"
    (runtime_dir / "context_cards").mkdir(parents=True)
    (runtime_dir / "price_snapshots").mkdir(parents=True)

    monkeypatch.setattr(data_loader, "LOGS_DIR", logs_dir)
    monkeypatch.setattr(data_loader, "RUNTIME_DIR", runtime_dir)

    # 이벤트 로그
    events = [
        {
            "type": "event", "mode": "paper",
            "event_id": "evt001", "ticker": "005930", "corp_name": "삼성전자",
            "source": "KIS",
            "headline": "삼성전자 공급계약 체결",
            "bucket": "POS_STRONG", "keyword_hits": ["공급계약"],
            "detected_at": "2026-03-19T09:10:00+09:00",
            "skip_stage": None, "skip_reason": None,
            "decision_action": "BUY", "decision_confidence": 85,
            "decision_size_hint": "M", "decision_reason": "공급계약 호재",
            "guardrail_result": None,
        },
        {
            "type": "event", "mode": "paper",
            "event_id": "evt002", "ticker": "000660", "corp_name": "SK하이닉스",
            "source": "KIS",
            "headline": "SK하이닉스 실적 발표",
            "bucket": "POS_WEAK", "keyword_hits": ["실적"],
            "detected_at": "2026-03-19T09:15:00+09:00",
            "skip_stage": None, "skip_reason": None,
            "decision_action": "SKIP", "decision_confidence": 60,
            "decision_size_hint": "S", "decision_reason": "약한 호재",
            "guardrail_result": None,
        },
        {
            "type": "event", "mode": "paper",
            "event_id": "evt003", "ticker": "035420", "corp_name": "NAVER",
            "source": "KIND",
            "headline": "네이버 일반 공시",
            "bucket": "UNKNOWN", "keyword_hits": [],
            "detected_at": "2026-03-19T09:20:00+09:00",
            "skip_stage": "BUCKET", "skip_reason": "UNKNOWN_BUCKET",
            "decision_action": None, "decision_confidence": None,
            "decision_size_hint": None, "decision_reason": None,
            "guardrail_result": None,
        },
        {
            "type": "event", "mode": "paper",
            "event_id": "evt004", "ticker": "051910", "corp_name": "LG화학",
            "source": "KIS",
            "headline": "LG화학 대형 공급계약 체결",
            "bucket": "POS_STRONG", "keyword_hits": ["공급계약"],
            "detected_at": "2026-03-19T09:30:00+09:00",
            "skip_stage": "GUARDRAIL", "skip_reason": "CHASE_BUY_BLOCKED",
            "decision_action": "BUY", "decision_confidence": 82,
            "decision_size_hint": "L", "decision_reason": "대형 계약 호재",
            "guardrail_result": "CHASE_BUY_BLOCKED",
        },
    ]
    log_path = logs_dir / "kindshot_20260319.jsonl"
    with open(log_path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # Context card
    ctx_records = [
        {
            "type": "context_card", "event_id": "evt001",
            "ticker": "005930", "corp_name": "삼성전자", "bucket": "POS_STRONG",
            "ctx": {"rsi_14": 55.0, "macd_hist": 0.3, "bb_position": 60.0,
                    "atr_14": 2.5, "ret_today": 1.2, "spread_bps": 15.0,
                    "adv_value_20d": 1e10, "vol_pct_20d": 65.0},
            "market_ctx": {"kospi_change_pct": 0.5, "kosdaq_change_pct": -0.2},
        },
    ]
    ctx_path = runtime_dir / "context_cards" / "20260319.jsonl"
    with open(ctx_path, "w") as f:
        for r in ctx_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Price snapshots
    snapshots = [
        {
            "type": "price_snapshot", "event_id": "evt001", "horizon": "t0",
            "ts": "2026-03-19T09:10:00Z", "px": 60000.0,
            "ret_long_vs_t0": 0.0, "spread_bps": 15.0,
        },
        {
            "type": "price_snapshot", "event_id": "evt001", "horizon": "t+5m",
            "ts": "2026-03-19T09:15:00Z", "px": 60600.0,
            "ret_long_vs_t0": 0.01, "spread_bps": 15.0,
        },
        {
            "type": "price_snapshot", "event_id": "evt001", "horizon": "t+30m",
            "ts": "2026-03-19T09:40:00Z", "px": 60300.0,
            "ret_long_vs_t0": 0.005, "spread_bps": 15.0,
        },
        {
            "type": "price_snapshot", "event_id": "shadow_evt004", "horizon": "t0",
            "ts": "2026-03-19T09:30:00Z", "px": 300000.0,
            "ret_long_vs_t0": 0.0, "spread_bps": 12.0,
        },
        {
            "type": "price_snapshot", "event_id": "shadow_evt004", "horizon": "t+5m",
            "ts": "2026-03-19T09:35:00Z", "px": 303000.0,
            "ret_long_vs_t0": 0.01, "spread_bps": 12.0,
        },
        {
            "type": "price_snapshot", "event_id": "shadow_evt004", "horizon": "t+30m",
            "ts": "2026-03-19T10:00:00Z", "px": 306000.0,
            "ret_long_vs_t0": 0.02, "spread_bps": 12.0,
        },
    ]
    snap_path = runtime_dir / "price_snapshots" / "20260319.jsonl"
    with open(snap_path, "w") as f:
        for s in snapshots:
            f.write(json.dumps(s) + "\n")

    return tmp_path


def test_read_jsonl(tmp_path):
    path = tmp_path / "test.jsonl"
    path.write_text('{"a":1}\n{"b":2}\n')
    result = _read_jsonl(path)
    assert len(result) == 2
    assert result[0]["a"] == 1


def test_read_jsonl_missing(tmp_path):
    result = _read_jsonl(tmp_path / "nonexistent.jsonl")
    assert result == []


def test_read_json(tmp_path):
    path = tmp_path / "test.json"
    path.write_text('{"key": "value"}')
    result = _read_json(path)
    assert result == {"key": "value"}


def test_read_json_missing(tmp_path):
    result = _read_json(tmp_path / "nope.json")
    assert result is None


def test_available_dates(fake_logs):
    dates = available_dates()
    assert "20260319" in dates


def test_load_events(fake_logs):
    df = load_events("20260319")
    assert len(df) == 4
    assert "event_id" in df.columns
    buys = df[df["decision_action"] == "BUY"]
    assert len(buys) == 2


def test_load_events_empty(fake_logs):
    df = load_events("20260101")
    assert df.empty


def test_load_context_cards(fake_logs):
    df = load_context_cards("20260319")
    assert len(df) == 1
    assert df.iloc[0]["rsi_14"] == 55.0
    assert df.iloc[0]["ticker"] == "005930"


def test_load_price_snapshots(fake_logs):
    df = load_price_snapshots("20260319")
    assert len(df) == 6
    assert set(df["horizon"]) == {"t0", "t+5m", "t+30m"}


def test_compute_trade_pnl(fake_logs):
    df = compute_trade_pnl("20260319")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "005930"
    assert row["best_ret_pct"] == 1.0  # 0.01 * 100
    assert row["final_ret_pct"] == 0.5  # 0.005 * 100
    assert row["confidence"] == 85


def test_compute_trade_pnl_empty(fake_logs):
    df = compute_trade_pnl("20260101")
    assert df.empty


def test_load_multi_day_events(fake_logs):
    df = load_multi_day_events(7)
    assert len(df) == 4
    assert "date" in df.columns


def test_compute_multi_day_pnl(fake_logs):
    df = compute_multi_day_pnl(7)
    assert len(df) == 1  # 1 date available
    row = df.iloc[0]
    assert row["trades"] == 1
    assert row["wins"] == 1
    assert row["losses"] == 0
    assert row["win_rate"] == 100.0
    assert row["cum_ret_pct"] == 0.5  # 0.005 * 100


def test_load_multi_day_pnl_detail(fake_logs):
    df = load_multi_day_pnl_detail(7)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "005930"
    assert "date" in df.columns
    assert "keyword_hits" in df.columns


def test_compute_multi_day_pnl_empty(fake_logs, monkeypatch):
    import data_loader
    monkeypatch.setattr(data_loader, "LOGS_DIR", fake_logs / "empty_logs")
    (fake_logs / "empty_logs").mkdir()
    df = compute_multi_day_pnl(7)
    assert df.empty


def test_load_health_offline():
    """서버가 없을 때 None 반환."""
    result = load_health()
    assert result is None


def test_compute_daily_equity_curve(fake_logs):
    df = compute_daily_equity_curve("20260319")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["cum_ret_pct"] == 0.5
    assert row["drawdown_pct"] == 0.0


def test_load_shadow_trade_pnl_and_summary(fake_logs):
    df = load_shadow_trade_pnl("20260319")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "051910"
    assert row["guardrail_result"] == "CHASE_BUY_BLOCKED"
    assert row["best_ret_pct"] == 2.0
    assert row["final_ret_pct"] == 2.0

    summary = summarize_shadow_trade_pnl("20260319")
    assert summary["blocked_buy_count"] == 1
    assert summary["shadow_trade_count"] == 1
    assert summary["win_rate"] == 100.0
    assert summary["total_ret_pct"] == 2.0


def test_shadow_summary_without_shadow_snapshots(fake_logs, monkeypatch):
    import data_loader

    original = load_price_snapshots("20260319")
    monkeypatch.setattr(
        data_loader,
        "load_price_snapshots",
        lambda date_str: original[~original["event_id"].astype(str).str.startswith("shadow_")].copy(),
    )
    summary = summarize_shadow_trade_pnl("20260319")
    assert summary["blocked_buy_count"] == 1
    assert summary["shadow_trade_count"] == 0
    assert summary["top_guardrail_reason"] == "CHASE_BUY_BLOCKED"


def test_load_live_feed(fake_logs):
    df = load_live_feed(limit=3, n_days=7)
    assert len(df) == 3
    assert list(df["ticker"]) == ["051910", "035420", "000660"]
    assert list(df["feed_action"]) == ["GUARDRAIL_BLOCKED", "BUCKET", "SKIP"]


def test_load_version_trend(fake_logs):
    df = load_version_trend()
    assert list(df["version"]) == ["v64", "v65", "v66"]
    v66 = df[df["version"] == "v66"].iloc[0]
    assert v66["sample_size"] == 1
    assert v66["win_rate"] == 100.0
    assert v66["total_ret_pct"] == 0.5
