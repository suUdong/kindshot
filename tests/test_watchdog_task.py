import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest

_KST = timezone(timedelta(hours=9))


@pytest.mark.asyncio
async def test_watchdog_notifies_when_feed_alive():
    """Watchdog should call sd_notify when feed is polling."""
    from kindshot.main import _watchdog_loop
    from kindshot.config import Config
    stop = asyncio.Event()
    feed = MagicMock()
    feed.last_poll_at = datetime.now(_KST)
    counters = MagicMock()
    counters.totals = {"events_seen": 5}

    with patch("kindshot.main.notify_watchdog") as mock_wd:
        async def stop_soon():
            await asyncio.sleep(0.1)
            stop.set()
        asyncio.create_task(stop_soon())
        config = Config(watchdog_interval_s=0.05)
        await _watchdog_loop(feed, counters, config, stop)
        mock_wd.assert_called()


@pytest.mark.asyncio
async def test_watchdog_skips_notify_when_feed_stale():
    """Watchdog should NOT notify when feed hasn't polled recently."""
    from kindshot.main import _watchdog_loop
    from kindshot.config import Config
    stop = asyncio.Event()
    feed = MagicMock()
    feed.last_poll_at = datetime.now(_KST) - timedelta(minutes=5)
    counters = MagicMock()
    counters.totals = {"events_seen": 0}

    with patch("kindshot.main.notify_watchdog") as mock_wd:
        async def stop_soon():
            await asyncio.sleep(0.1)
            stop.set()
        asyncio.create_task(stop_soon())
        config = Config(watchdog_interval_s=0.05, watchdog_stale_threshold_s=60.0)
        await _watchdog_loop(feed, counters, config, stop)
        mock_wd.assert_not_called()
