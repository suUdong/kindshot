"""Tests for price snapshot scheduling."""

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.kis_client import PriceInfo
from kindshot.models import T0Basis
from kindshot.price import PriceFetcher, SnapshotScheduler, HORIZON_OFFSETS


def test_schedule_creates_all_horizons():
    """schedule_t0 should create t0, t+30s, t+1m, t+2m, t+5m, t+30m, close snapshots."""
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    scheduler.schedule_t0(
        event_id="evt1", ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=datetime.now(timezone.utc), run_id="run1",
    )

    horizons = {s.horizon for s in scheduler._heap}
    assert horizons == {"t0", "t+30s", "t+1m", "t+2m", "t+5m", "t+15m", "t+20m", "t+30m", "close"}


def test_close_snapshot_uses_config_delay():
    """close_snapshot_delay_s should shift the close snapshot fire time."""
    cfg = Config(close_snapshot_delay_s=600.0)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    # Mock datetime.now to return 09:00 KST (well before market close)
    kst = timezone(timedelta(hours=9))
    morning = datetime(2026, 3, 5, 9, 0, 0, tzinfo=kst)

    with patch("kindshot.price.datetime") as mock_dt:
        mock_dt.now.return_value = morning
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        scheduler.schedule_t0(
            event_id="evt1", ticker="005930",
            t0_basis=T0Basis.DETECTED_AT,
            t0_ts=morning, run_id="run1",
        )

    close_snap = [s for s in scheduler._heap if s.horizon == "close"][0]
    # 15:30 + 600s = 15:40 KST. From 09:00 that's 6h40m = 24000s
    # fire_at = now_mono + seconds_until_close
    # seconds_until_close should be (15:40 - 09:00) = 24000s
    t0_snap = [s for s in scheduler._heap if s.horizon == "t0"][0]
    close_offset = close_snap.fire_at - t0_snap.fire_at
    # t0 fires immediately, close fires ~24000s later
    assert abs(close_offset - 24000.0) < 2.0


async def test_scheduler_stop_interrupts_sleep():
    """stop() should interrupt scheduler loop sleep quickly."""
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    scheduler.stop()
    await asyncio.wait_for(task, timeout=0.5)
    assert time.monotonic() - t0 < 0.5


async def test_paper_buy_applies_half_spread_to_returns():
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    prices = [
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_000_000.0, fetch_latency_ms=10),
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_100_000.0, fetch_latency_ms=10),
    ]
    scheduler._fetcher.fetch = AsyncMock(side_effect=prices)

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
    )

    snaps = sorted([s for s in scheduler._heap if s.horizon in {"t0", "t+1m"}], key=lambda s: s.fire_at)
    await scheduler._fire(snaps[0])
    await scheduler._fire(snaps[1])

    t0_record = log.write.await_args_list[0].args[0]
    t1_record = log.write.await_args_list[1].args[0]
    assert t0_record.ret_long_vs_t0 == 0.0
    assert t1_record.ret_long_vs_t0 == pytest.approx(-0.0009990009990008542)


async def test_live_mode_keeps_unadjusted_returns():
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(side_effect=[
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_000_000.0, fetch_latency_ms=10),
        PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_100_000.0, fetch_latency_ms=10),
    ])

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="live",
        is_buy_decision=True,
    )

    snaps = sorted([s for s in scheduler._heap if s.horizon in {"t0", "t+1m"}], key=lambda s: s.fire_at)
    await scheduler._fire(snaps[0])
    await scheduler._fire(snaps[1])

    t1_record = log.write.await_args_list[1].args[0]
    assert t1_record.ret_long_vs_t0 == 0.0


async def test_scheduler_persists_runtime_price_snapshots(tmp_path):
    cfg = Config(
        runtime_price_snapshots_dir=tmp_path / "data" / "runtime" / "price_snapshots",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
    )
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(px=10000.0, open_px=10000.0, spread_bps=20.0, cum_value=1_000_000.0, fetch_latency_ms=10))

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
        mode="paper",
        is_buy_decision=True,
    )

    snap = sorted([s for s in scheduler._heap if s.horizon == "t0"], key=lambda s: s.fire_at)[0]
    await scheduler._fire(snap)

    files = list((tmp_path / "data" / "runtime" / "price_snapshots").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "price_snapshot"
    assert rows[0]["event_id"] == "evt1"
    assert rows[0]["horizon"] == "t0"
    assert rows[0]["px"] == 10000.0

    index_payload = json.loads((tmp_path / "data" / "runtime" / "index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["date"]
    assert index_payload["entries"][0]["artifacts"]["price_snapshots"]["exists"] is True


async def _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=10.0):
    """Helper: create scheduler, schedule t0, fire t0 to set entry price."""
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=t0_px, open_px=t0_px, spread_bps=spread_bps,
        cum_value=1_000_000.0, fetch_latency_ms=10,
    ))
    scheduler.schedule_t0(
        event_id="evt1", ticker="005930",
        t0_basis=T0Basis.DECIDED_AT,
        t0_ts=datetime.now(timezone.utc), run_id="run1",
        mode="paper", is_buy_decision=True,
    )
    # Fire t0 to record entry price
    t0_snap = [s for s in scheduler._heap if s.horizon == "t0"][0]
    await scheduler._fire(t0_snap)
    return scheduler, log


async def test_paper_take_profit_triggers():
    """TP 0.8%: t0=10000, t+1m=10200 (+2%) → TP hit."""
    cfg = Config(paper_take_profit_pct=0.8, paper_stop_loss_pct=-1.0, trailing_stop_enabled=False)
    scheduler, log = await _make_scheduler_with_t0(cfg)

    # Fire t+1m with price up 2%
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10200.0, open_px=10000.0, spread_bps=10.0,
        cum_value=1_200_000.0, fetch_latency_ms=10,
    ))
    t1_snap = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(t1_snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


async def test_paper_stop_loss_triggers():
    """SL -1.0%: t0=10000, t+30s=9850 (-1.5%) → SL hit."""
    cfg = Config(paper_take_profit_pct=1.5, paper_stop_loss_pct=-1.0, trailing_stop_enabled=False)
    scheduler, log = await _make_scheduler_with_t0(cfg)

    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9850.0, open_px=10000.0, spread_bps=10.0,
        cum_value=900_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+30s"


async def test_paper_trailing_stop_triggers():
    """Trailing stop: peak 1.5% → drop to 0.5% (peak - 0.8% trail) → exit."""
    cfg = Config(
        paper_take_profit_pct=5.0,  # high TP so it doesn't trigger
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.8,
        trailing_stop_pct=0.8,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # t+30s: +1.5% (above activation 0.8%) — sets peak
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10150.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_100_000.0, fetch_latency_ms=10,
    ))
    snap1 = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap1)
    assert "evt1" not in scheduler._virtual_exits  # not yet

    # t+1m: +0.5% (dropped from peak 1.5%, diff = 1.0% > trail 0.8%) → trailing stop
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10050.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_050_000.0, fetch_latency_ms=10,
    ))
    snap2 = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap2)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


async def test_paper_max_hold_triggers():
    """Max hold 30min: at t+30m horizon → forced exit."""
    cfg = Config(
        paper_take_profit_pct=10.0,  # high — won't trigger
        paper_stop_loss_pct=-10.0,
        trailing_stop_enabled=False,
        max_hold_minutes=30,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg)

    # t+30m: price unchanged — max hold triggers
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10000.0, open_px=10000.0, spread_bps=10.0,
        cum_value=1_000_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+30m"][0]
    await scheduler._fire(snap)

    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+30m"


async def test_virtual_exit_prevents_double_trigger():
    """Once TP fires, SL should not fire on subsequent snapshots."""
    cfg = Config(paper_take_profit_pct=1.0, paper_stop_loss_pct=-1.0, trailing_stop_enabled=False)
    scheduler, log = await _make_scheduler_with_t0(cfg)

    # t+30s: +2% → TP hit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10200.0, open_px=10000.0, spread_bps=10.0,
        cum_value=1_200_000.0, fetch_latency_ms=10,
    ))
    snap1 = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap1)
    assert scheduler._virtual_exits["evt1"] == "t+30s"

    # t+1m: -5% crash — should NOT change exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=9500.0, open_px=10000.0, spread_bps=10.0,
        cum_value=800_000.0, fetch_latency_ms=10,
    ))
    snap2 = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap2)
    assert scheduler._virtual_exits["evt1"] == "t+30s"  # unchanged


async def test_trailing_stop_early_tier_tight():
    """0~5분 구간: early trailing (0.3%) 적용. peak 0.5% → drop to 0.1% (diff 0.4% > 0.3%) → exit."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_early_pct=0.3,
        trailing_stop_mid_pct=0.5,
        trailing_stop_late_pct=0.7,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # t+30s: +0.5% (above activation 0.3%) — sets peak, within early tier (< 5min)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10050.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_100_000.0, fetch_latency_ms=10,
    ))
    snap1 = [s for s in scheduler._heap if s.horizon == "t+30s"][0]
    await scheduler._fire(snap1)
    assert "evt1" not in scheduler._virtual_exits

    # t+1m: +0.1% (dropped 0.4% from peak 0.5%, > early trail 0.3%) → exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10010.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_010_000.0, fetch_latency_ms=10,
    ))
    snap2 = [s for s in scheduler._heap if s.horizon == "t+1m"][0]
    await scheduler._fire(snap2)
    assert "evt1" in scheduler._virtual_exits
    assert scheduler._virtual_exits["evt1"] == "t+1m"


async def test_trailing_stop_mid_tier():
    """5~30분 구간: mid trailing (0.5%) 적용. 시간을 인위적으로 조작."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_early_pct=0.3,
        trailing_stop_mid_pct=0.5,
        trailing_stop_late_pct=0.7,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # Simulate 6 minutes elapsed (mid tier)
    scheduler._entry_times["evt1"] = time.monotonic() - 360

    # Set peak at 1.0%
    scheduler._peak_returns["evt1"] = 1.0

    # Price at +0.4% (dropped 0.6% from peak 1.0%, > mid trail 0.5%) → exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10040.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_040_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)
    assert "evt1" in scheduler._virtual_exits


async def test_trailing_stop_mid_tier_no_exit_within_tolerance():
    """5~30분 구간: peak 대비 drop이 mid trail 이내면 exit 안 함."""
    cfg = Config(
        paper_take_profit_pct=5.0,
        paper_stop_loss_pct=-5.0,
        trailing_stop_enabled=True,
        trailing_stop_activation_pct=0.3,
        trailing_stop_early_pct=0.3,
        trailing_stop_mid_pct=0.5,
        trailing_stop_late_pct=0.7,
    )
    scheduler, log = await _make_scheduler_with_t0(cfg, t0_px=10000.0, spread_bps=0.0)

    # Simulate 6 minutes elapsed (mid tier)
    scheduler._entry_times["evt1"] = time.monotonic() - 360

    # Set peak at 1.0%
    scheduler._peak_returns["evt1"] = 1.0

    # Price at +0.6% (dropped 0.4% from peak 1.0%, < mid trail 0.5%) → NO exit
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(
        px=10060.0, open_px=10000.0, spread_bps=0.0,
        cum_value=1_060_000.0, fetch_latency_ms=10,
    ))
    snap = [s for s in scheduler._heap if s.horizon == "t+5m"][0]
    await scheduler._fire(snap)
    assert "evt1" not in scheduler._virtual_exits


async def test_tp_default_lowered_to_0_8():
    """TP 기본값이 0.8%로 낮아졌는지 확인."""
    cfg = Config()
    assert cfg.paper_take_profit_pct == 0.8


async def test_trailing_activation_default_lowered_to_0_3():
    """Trailing stop activation 기본값이 0.3%로 낮아졌는지 확인."""
    cfg = Config()
    assert cfg.trailing_stop_activation_pct == 0.3


async def test_flush_close_on_shutdown_fires_pending_close_after_cutoff():
    cfg = Config(close_snapshot_delay_s=300.0)
    fetcher = PriceFetcher(kis=None)
    log = MagicMock()
    log.write = AsyncMock()
    scheduler = SnapshotScheduler(cfg, fetcher, log)
    scheduler._fetcher.fetch = AsyncMock(return_value=PriceInfo(px=10000.0, open_px=10000.0, spread_bps=10.0, cum_value=1_000_000.0, fetch_latency_ms=10))

    event_ts = datetime(2026, 3, 5, 14, 55, tzinfo=timezone.utc)
    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=event_ts,
        run_id="run1",
    )
    close_count_before = len([s for s in scheduler._heap if s.horizon == "close"])
    assert close_count_before == 1

    with patch("kindshot.price.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 6, 15, 36, tzinfo=timezone(timedelta(hours=9)))
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        flushed = await scheduler.flush_close_on_shutdown()

    assert flushed == 1
    assert len([s for s in scheduler._heap if s.horizon == "close"]) == 0


async def test_flush_close_on_shutdown_skips_before_cutoff():
    cfg = Config(close_snapshot_delay_s=300.0)
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    scheduler.schedule_t0(
        event_id="evt1",
        ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=datetime.now(timezone.utc),
        run_id="run1",
    )

    with patch("kindshot.price.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 6, 15, 34, tzinfo=timezone(timedelta(hours=9)))
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        flushed = await scheduler.flush_close_on_shutdown()

    assert flushed == 0
    assert len([s for s in scheduler._heap if s.horizon == "close"]) == 1
