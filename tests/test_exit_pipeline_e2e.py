from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.bucket import Bucket
from kindshot.config import Config
from kindshot.event_registry import ProcessedEvent
from kindshot.feed import RawDisclosure
from kindshot.guardrails import GuardrailState
from kindshot.kis_client import PriceInfo
from kindshot.main import _handle_trade_close
from kindshot.models import EventIdMethod, EventKind, MarketContext, T0Basis
from kindshot.performance import PerformanceTracker
from kindshot.pipeline import execute_bucket_path, process_registered_event
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


@pytest.mark.asyncio
async def test_pipeline_neg_strong_liquidation_e2e_closes_remaining_partial_position(tmp_path):
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
        paper=True,
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
            PriceInfo(px=10_150.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_150_000.0, fetch_latency_ms=10),
            PriceInfo(px=10_140.0, open_px=10_000.0, spread_bps=0.0, cum_value=1_140_000.0, fetch_latency_ms=10),
        ]
    )
    scheduler.schedule_t0(
        event_id="buy_evt",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
        confidence=82,
        size_hint="M",
    )

    snaps = {snap.horizon: snap for snap in scheduler._heap if snap.horizon in {"t0", "t+30s", "close"}}
    raw = RawDisclosure(
        title="삼성전자(005930) - 대규모 손상차손 및 실적 쇼크",
        link="https://example.com/neg",
        rss_guid="neg-guid",
        published="2026-03-29T10:02:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    processed = ProcessedEvent(
        event_id="evt_neg",
        event_id_method=EventIdMethod.UID,
        event_kind=EventKind.ORIGINAL,
        parent_id=None,
        event_group_id="evt_neg",
        parent_match_method=None,
        parent_match_score=None,
        parent_candidate_count=None,
        kind_uid=None,
        raw=raw,
    )
    market = MagicMock()
    market.snapshot = MarketContext()

    with patch("kindshot.main.try_send_sell_signal"):
        await scheduler._fire(snaps["t0"])
        await scheduler._fire(snaps["t+30s"])
        outcome = await execute_bucket_path(
            raw=raw,
            processed=processed,
            bucket=Bucket.NEG_STRONG,
            keyword_hits=[],
            decision_engine=MagicMock(),
            market=market,
            scheduler=scheduler,
            log=log,
            config=cfg,
            run_id="run1",
            kis=None,
            counters=None,
            mode="paper",
            guardrail_state=guardrail_state,
            feed_source="KIND",
        )
        await scheduler._fire(snaps["close"])

    assert outcome.skip_reason == "NEG_BUCKET"
    assert scheduler.has_open_position("005930") is False

    summary = performance_tracker.daily_summary()
    assert summary.total_trades == 1
    trade = summary.trades[0]
    assert trade.event_id == "buy_evt"
    assert trade.exit_type == "news_exit"
    assert trade.position_closed is True
    assert trade.size_won == pytest.approx(5_000_000.0)
    assert trade.exit_px == pytest.approx(10_175.0)
    assert trade.pnl_won == pytest.approx(87_500.0)
    assert trade.cumulative_pnl_won == pytest.approx(87_500.0)
    assert trade.cumulative_ret_pct == pytest.approx(1.75)
    assert guardrail_state.daily_pnl == pytest.approx(87_500.0)
    assert guardrail_state.position_count == 0
    assert guardrail_state.recent_trade_outcomes == [True]

    rows = _read_trade_rows(cfg.data_dir / "performance")
    assert len(rows) == 1
    assert rows[0]["exit_type"] == "news_exit"
    assert rows[0]["cumulative_ret_pct"] == pytest.approx(1.75)


@pytest.mark.asyncio
async def test_process_registered_correction_liquidation_e2e_records_final_trade(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        order_size=1_000_000,
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=False,
        partial_take_profit_enabled=False,
        paper=True,
        news_exit_enabled=True,
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
            PriceInfo(px=9_800.0, open_px=10_000.0, spread_bps=0.0, cum_value=950_000.0, fetch_latency_ms=10),
            PriceInfo(px=9_750.0, open_px=10_000.0, spread_bps=0.0, cum_value=930_000.0, fetch_latency_ms=10),
        ]
    )
    scheduler.schedule_t0(
        event_id="buy_evt",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
        confidence=76,
        size_hint="M",
    )

    t0_snap = next(snap for snap in scheduler._heap if snap.horizon == "t0")
    close_snap = next(snap for snap in scheduler._heap if snap.horizon == "close")
    raw = RawDisclosure(
        title="삼성전자(005930) - 정정 공시",
        link="https://example.com/correction",
        rss_guid="corr-guid",
        published="2026-03-29T10:04:00+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )
    processed = ProcessedEvent(
        event_id="evt_corr",
        event_id_method=EventIdMethod.UID,
        event_kind=EventKind.CORRECTION,
        parent_id="buy_evt",
        event_group_id="evt_corr",
        parent_match_method=None,
        parent_match_score=None,
        parent_candidate_count=None,
        kind_uid=None,
        raw=raw,
    )
    market = MagicMock()
    market.snapshot = MarketContext()

    with patch("kindshot.main.try_send_sell_signal"):
        await scheduler._fire(t0_snap)
        await process_registered_event(
            raw=raw,
            processed=processed,
            decision_engine=MagicMock(),
            market=market,
            scheduler=scheduler,
            log=log,
            config=cfg,
            run_id="run1",
            kis=None,
            counters=None,
            mode="paper",
            guardrail_state=guardrail_state,
            feed_source="KIND",
        )
        await scheduler._fire(close_snap)

    assert scheduler.has_open_position("005930") is False

    summary = performance_tracker.daily_summary()
    assert summary.total_trades == 1
    trade = summary.trades[0]
    assert trade.event_id == "buy_evt"
    assert trade.exit_type == "correction_exit"
    assert trade.position_closed is True
    assert trade.pnl_won == pytest.approx(-20_000.0)
    assert trade.cumulative_ret_pct == pytest.approx(-2.0)
    assert guardrail_state.daily_pnl == pytest.approx(-20_000.0)
    assert guardrail_state.position_count == 0
    assert guardrail_state.recent_trade_outcomes == [False]

    rows = _read_trade_rows(cfg.data_dir / "performance")
    assert len(rows) == 1
    assert rows[0]["exit_type"] == "correction_exit"
