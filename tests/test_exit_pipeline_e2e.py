from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.guardrails import GuardrailState
from kindshot.kis_client import PriceInfo
from kindshot.main import _handle_trade_close
from kindshot.models import T0Basis
from kindshot.performance import PerformanceTracker
from kindshot.price import PriceFetcher, SnapshotScheduler


def _read_trade_rows(performance_dir):
    files = list(performance_dir.glob("*_trades.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.asyncio
async def test_exit_pipeline_e2e_records_take_profit_once(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        order_size=1_000_000,
        paper_take_profit_pct=1.5,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=False,
        partial_take_profit_enabled=False,
    )
    guardrail_state = GuardrailState(cfg, state_dir=tmp_path / "state")
    guardrail_state.record_buy("005930")
    performance_tracker = PerformanceTracker(cfg.data_dir)

    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(
        cfg,
        PriceFetcher(kis=None),
        log,
        trade_close_callback=lambda **kwargs: _handle_trade_close(
            guardrail_state=guardrail_state,
            performance_tracker=performance_tracker,
            **kwargs,
        ),
    )
    scheduler._fetcher.fetch = AsyncMock(
        side_effect=[
            PriceInfo(px=10_000.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_000_000.0, fetch_latency_ms=10),
            PriceInfo(px=10_200.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_200_000.0, fetch_latency_ms=10),
            PriceInfo(px=10_300.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_300_000.0, fetch_latency_ms=10),
        ]
    )
    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
        confidence=82,
        size_hint="M",
    )

    snaps = {snap.horizon: snap for snap in scheduler._heap if snap.horizon in {"t0", "t+1m", "close"}}
    with patch("kindshot.main.try_send_sell_signal"):
        await scheduler._fire(snaps["t0"])
        await scheduler._fire(snaps["t+1m"])
        await scheduler._fire(snaps["close"])

    summary = performance_tracker.daily_summary()
    assert summary.total_trades == 1
    trade = summary.trades[0]
    assert trade.event_id == "evt1"
    assert trade.exit_type == "take_profit"
    assert trade.position_closed is True
    assert trade.pnl_pct == pytest.approx(2.0)
    assert trade.pnl_won == pytest.approx(20_000.0)
    assert trade.cumulative_ret_pct == pytest.approx(2.0)
    assert guardrail_state.daily_pnl == pytest.approx(20_000.0)
    assert guardrail_state.position_count == 0
    assert guardrail_state.recent_trade_outcomes == [True]

    rows = _read_trade_rows(cfg.data_dir / "performance")
    assert len(rows) == 1
    assert rows[0]["exit_type"] == "take_profit"
    assert rows[0]["cumulative_ret_pct"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_exit_pipeline_e2e_persists_only_final_partial_close_result(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        order_size=5_000_000,
        paper_take_profit_pct=2.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        partial_take_profit_enabled=True,
        partial_take_profit_size_pct=50.0,
        trailing_stop_post_partial_early_pct=0.2,
    )
    guardrail_state = GuardrailState(cfg, state_dir=tmp_path / "state")
    guardrail_state.record_buy("005930")
    performance_tracker = PerformanceTracker(cfg.data_dir)

    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(
        cfg,
        PriceFetcher(kis=None),
        log,
        trade_close_callback=lambda **kwargs: _handle_trade_close(
            guardrail_state=guardrail_state,
            performance_tracker=performance_tracker,
            **kwargs,
        ),
    )
    scheduler._fetcher.fetch = AsyncMock(
        side_effect=[
            PriceInfo(px=10_000.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_000_000.0, fetch_latency_ms=10),
            PriceInfo(px=10_200.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_200_000.0, fetch_latency_ms=10),
            PriceInfo(px=10_180.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_180_000.0, fetch_latency_ms=10),
        ]
    )
    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
        confidence=82,
        size_hint="M",
    )

    snaps = {snap.horizon: snap for snap in scheduler._heap if snap.horizon in {"t0", "t+30s", "t+1m"}}
    with patch("kindshot.main.try_send_sell_signal"):
        await scheduler._fire(snaps["t0"])
        await scheduler._fire(snaps["t+30s"])
        await scheduler._fire(snaps["t+1m"])

    summary = performance_tracker.daily_summary()
    assert summary.total_trades == 1
    trade = summary.trades[0]
    assert trade.event_id == "evt1"
    assert trade.exit_type == "trailing_stop"
    assert trade.position_closed is True
    assert trade.size_won == pytest.approx(5_000_000.0)
    assert trade.pnl_won == pytest.approx(95_000.0)
    assert trade.cumulative_pnl_won == pytest.approx(95_000.0)
    assert trade.cumulative_ret_pct == pytest.approx(1.9)
    assert guardrail_state.daily_pnl == pytest.approx(95_000.0)
    assert guardrail_state.position_count == 0
    assert guardrail_state.recent_trade_outcomes == [True]

    rows = _read_trade_rows(cfg.data_dir / "performance")
    assert len(rows) == 1
    assert rows[0]["exit_type"] == "trailing_stop"
    assert rows[0]["cumulative_pnl_won"] == pytest.approx(95_000.0)

