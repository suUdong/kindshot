"""Tests for price snapshot scheduling."""

import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from kindshot.config import Config
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
