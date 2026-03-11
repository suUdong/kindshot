"""Tests for price snapshot scheduling."""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.kis_client import PriceInfo
from kindshot.models import T0Basis
from kindshot.price import PriceFetcher, SnapshotScheduler, HORIZON_OFFSETS


def test_schedule_creates_all_horizons():
    """schedule_t0 should create t0, t+1m, t+5m, t+30m, close snapshots."""
    cfg = Config()
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, MagicMock())

    scheduler.schedule_t0(
        event_id="evt1", ticker="005930",
        t0_basis=T0Basis.DETECTED_AT,
        t0_ts=datetime.now(timezone.utc), run_id="run1",
    )

    horizons = {s.horizon for s in scheduler._heap}
    assert horizons == {"t0", "t+1m", "t+5m", "t+30m", "close"}


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
