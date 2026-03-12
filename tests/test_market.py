"""Tests for MarketMonitor with KOSPI/KOSDAQ/VKOSPI."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kindshot.config import Config
from kindshot.kis_client import IndexInfo
from kindshot.market import MarketMonitor
from kindshot.models import MarketContext


def _cfg(**kw) -> Config:
    return Config(**kw)


async def test_snapshot_default_none():
    monitor = MarketMonitor(_cfg())
    snap = monitor.snapshot
    assert snap.kospi_change_pct is None
    assert snap.kosdaq_change_pct is None
    assert snap.vkospi is None


async def test_initial_state_is_halted():
    """MarketMonitor should start halted (fail-close) until first successful update."""
    monitor = MarketMonitor(_cfg())
    assert monitor.is_halted is True
    assert monitor.is_initialized is False


async def test_update_with_kis():
    mock_kis = AsyncMock()
    mock_kis.get_index_info = AsyncMock(
        side_effect=lambda iscd: IndexInfo(
            iscd=iscd,
            change_pct=-0.5 if iscd == "0001" else 0.3,
            fetch_latency_ms=10,
            up_issue_count=500 if iscd == "0001" else 700,
            down_issue_count=400 if iscd == "0001" else 350,
        )
    )

    monitor = MarketMonitor(_cfg(), kis=mock_kis)

    with patch("kindshot.market._fetch_vkospi", new_callable=AsyncMock, return_value=18.5):
        await monitor.update()

    snap = monitor.snapshot
    assert snap.kospi_change_pct == -0.5
    assert snap.kosdaq_change_pct == 0.3
    assert snap.kospi_breadth_ratio == pytest.approx(1.25)
    assert snap.kosdaq_breadth_ratio == pytest.approx(2.0)
    assert snap.vkospi == 18.5
    assert monitor.is_halted is False  # -0.5 > -1.0
    assert monitor.is_initialized is True


async def test_halt_triggered():
    mock_kis = AsyncMock()
    mock_kis.get_index_info = AsyncMock(
        side_effect=lambda iscd: IndexInfo(
            iscd=iscd,
            change_pct=-1.5 if iscd == "0001" else -2.0,
            fetch_latency_ms=10,
            up_issue_count=200,
            down_issue_count=900,
        )
    )

    monitor = MarketMonitor(_cfg(kospi_halt_pct=-1.0), kis=mock_kis)

    with patch("kindshot.market._fetch_vkospi", new_callable=AsyncMock, return_value=25.0):
        await monitor.update()

    assert monitor.is_halted is True
    snap = monitor.snapshot
    assert snap.kospi_change_pct == -1.5
    assert snap.vkospi == 25.0


async def test_breadth_ratio_none_when_counts_missing():
    mock_kis = AsyncMock()
    mock_kis.get_index_info = AsyncMock(
        side_effect=lambda iscd: IndexInfo(iscd=iscd, change_pct=-0.2, fetch_latency_ms=10)
    )

    monitor = MarketMonitor(_cfg(), kis=mock_kis)

    with patch("kindshot.market._fetch_vkospi", new_callable=AsyncMock, return_value=None):
        await monitor.update()

    snap = monitor.snapshot
    assert snap.kospi_breadth_ratio is None
    assert snap.kosdaq_breadth_ratio is None


async def test_no_kis_no_update():
    monitor = MarketMonitor(_cfg())
    with patch("kindshot.market._fetch_vkospi", new_callable=AsyncMock, return_value=15.0):
        await monitor.update()
    # KOSPI/KOSDAQ should remain None (no KIS), but VKOSPI should update
    snap = monitor.snapshot
    assert snap.kospi_change_pct is None
    assert snap.vkospi == 15.0
    # Without KIS, market monitor never initializes — stays halted (fail-close)
    assert monitor.is_halted is True
    assert monitor.is_initialized is False


async def test_snapshot_returns_market_context():
    monitor = MarketMonitor(_cfg())
    snap = monitor.snapshot
    assert isinstance(snap, MarketContext)
