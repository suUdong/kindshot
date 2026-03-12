"""Tests for replay mode."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.replay import _load_actionable_events, _summarize_returns, replay


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    log_file = tmp_path / "test.jsonl"
    with open(log_file, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return log_file


def _make_event(bucket="POS_STRONG", quant_passed=True, skip_stage=None) -> dict:
    return {
        "type": "event",
        "mode": "paper",
        "schema_version": "0.1.2",
        "run_id": "test_run",
        "event_id": "evt_001",
        "event_id_method": "UID",
        "event_group_id": "evt_001",
        "detected_at": "2026-03-05T09:12:04+09:00",
        "disclosed_at": "2026-03-05T09:12:00+09:00",
        "ticker": "005930",
        "corp_name": "삼성전자",
        "headline": "반도체 공급계약 체결",
        "bucket": bucket,
        "quant_check_passed": quant_passed,
        "skip_stage": skip_stage,
        "ctx": {
            "ret_today": 3.5,
            "adv_value_20d": 10e9,
            "spread_bps": 8.0,
        },
    }


def test_load_deduplicates_by_event_id(tmp_path):
    """Duplicate event_ids should be deduplicated."""
    evt = _make_event()
    evt2 = _make_event()  # same event_id "evt_001"
    evt2["headline"] = "다른 공시"
    log_file = _write_events(tmp_path, [evt, evt2])
    result = _load_actionable_events(log_file)
    assert len(result) == 1


def test_load_different_event_ids(tmp_path):
    """Different event_ids should both be loaded."""
    evt1 = _make_event()
    evt2 = _make_event()
    evt2["event_id"] = "evt_002"
    log_file = _write_events(tmp_path, [evt1, evt2])
    result = _load_actionable_events(log_file)
    assert len(result) == 2


def test_load_actionable_events_filters(tmp_path):
    events = [
        _make_event(bucket="POS_STRONG", quant_passed=True),
        _make_event(bucket="NEG_STRONG", quant_passed=True),
        _make_event(bucket="POS_STRONG", quant_passed=False),
        {"type": "decision", "action": "BUY"},
    ]
    log_file = _write_events(tmp_path, events)
    result = _load_actionable_events(log_file)
    assert len(result) == 1
    assert result[0]["ticker"] == "005930"


def test_load_empty_file(tmp_path):
    log_file = tmp_path / "empty.jsonl"
    log_file.write_text("")
    result = _load_actionable_events(log_file)
    assert result == []


def test_summarize_returns_reports_drawdown_and_profit_factor():
    summary = _summarize_returns([10.0, -5.0, 2.0, -1.0])
    assert summary["trade_count"] == 4.0
    assert summary["win_rate_pct"] == 50.0
    assert summary["avg_return_pct"] == pytest.approx(1.5)
    assert summary["best_pct"] == 10.0
    assert summary["worst_pct"] == -5.0
    assert summary["max_drawdown_pct"] == pytest.approx(-5.0, abs=0.2)
    assert summary["profit_factor"] == pytest.approx(2.0)


def test_summarize_returns_handles_all_winners():
    summary = _summarize_returns([1.0, 2.0])
    assert summary["avg_loss_pct"] == 0.0
    assert summary["profit_factor"] == float("inf")


async def test_replay_no_events(tmp_path):
    log_file = _write_events(tmp_path, [{"type": "event", "bucket": "NEG_STRONG"}])
    cfg = Config(log_dir=tmp_path / "replay_logs")
    await replay(log_file, cfg)
    # Should complete without error


async def test_replay_uses_price_snapshots(tmp_path):
    """Replay should prefer price_snapshot t0/close over pykrx fallback."""
    evt = _make_event()
    t0_snap = {"type": "price_snapshot", "event_id": "evt_001", "horizon": "t0", "px": 70000.0}
    close_snap = {"type": "price_snapshot", "event_id": "evt_001", "horizon": "close", "px": 72100.0}
    log_file = _write_events(tmp_path, [evt, t0_snap, close_snap])
    cfg = Config(log_dir=tmp_path / "replay_logs", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 85
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)
        mock_prices.return_value = {}  # Should NOT be used

        await replay(log_file, cfg)
        # pykrx fallback should NOT have been called since snapshots exist
        mock_prices.assert_not_called()


async def test_replay_with_buy_decision(tmp_path):
    events = [_make_event()]
    log_file = _write_events(tmp_path, events)
    cfg = Config(log_dir=tmp_path / "replay_logs", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 80
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)
        mock_prices.return_value = {"open": 70000, "close": 72000, "high": 73000, "low": 69000}

        await replay(log_file, cfg)


async def test_replay_passes_normalized_guardrail_context(tmp_path):
    evt = _make_event()
    evt["ctx"]["intraday_value_vs_adv20d"] = 0.005
    evt["ctx"]["top_ask_notional"] = 4_000_000.0
    evt["ctx"]["quote_temp_stop"] = True
    evt["ctx"]["quote_liquidation_trade"] = False
    log_file = _write_events(tmp_path, [evt])
    cfg = Config(log_dir=tmp_path / "replay_logs", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 80
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=False, reason="TEMP_STOP")
        mock_prices.return_value = {}

        await replay(log_file, cfg)

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["intraday_value_vs_adv20d"] == 0.005
    assert mock_gr.call_args.kwargs["top_ask_notional"] == 4_000_000.0
    assert mock_gr.call_args.kwargs["quote_temp_stop"] is True
    assert mock_gr.call_args.kwargs["quote_liquidation_trade"] is False
