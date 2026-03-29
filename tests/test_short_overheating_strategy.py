"""Tests for short_overheating_strategy: 폴링, D+2 시그널 생성."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.krx_short_overheating import OverheatingRecord
from kindshot.models import Action, SizeHint
from kindshot.short_overheating_strategy import ShortOverheatingStrategy
from kindshot.strategy import SignalSource


def _make_config(**overrides):
    config = MagicMock()
    config.short_overheating_enabled = overrides.get("enabled", True)
    config.short_overheating_base_confidence = overrides.get("base_confidence", 60)
    config.short_overheating_poll_interval_s = overrides.get("poll_interval", 1.0)
    config.short_overheating_lookback_days = overrides.get("lookback_days", 7)
    config.short_overheating_d_offset = overrides.get("d_offset", 2)
    config.short_overheating_min_overheating_days = overrides.get("min_days", 1)
    return config


def _make_record(
    ticker="005930",
    corp_name="삼성전자",
    release_date=date(2026, 3, 25),
    overheating_days=3,
) -> OverheatingRecord:
    return OverheatingRecord(
        ticker=ticker,
        corp_name=corp_name,
        market="STK",
        designation_date=date(2026, 3, 20),
        release_date=release_date,
        designation_type="해제",
        overheating_days=overheating_days,
    )


# ── Properties ──────────────────────────────────────────


class TestProperties:
    def test_name(self):
        strategy = ShortOverheatingStrategy(_make_config(), session=MagicMock())
        assert strategy.name == "short_overheating"

    def test_source(self):
        strategy = ShortOverheatingStrategy(_make_config(), session=MagicMock())
        assert strategy.source == SignalSource.TECHNICAL

    def test_enabled(self):
        strategy = ShortOverheatingStrategy(_make_config(enabled=True), session=MagicMock())
        assert strategy.enabled is True

    def test_disabled(self):
        strategy = ShortOverheatingStrategy(_make_config(enabled=False), session=MagicMock())
        assert strategy.enabled is False


# ── _build_signal ───────────────────────────────────────


class TestBuildSignal:
    def test_generates_buy_signal(self):
        strategy = ShortOverheatingStrategy(_make_config(), session=MagicMock())
        record = _make_record(overheating_days=5)
        signal = strategy._build_signal(record, drop_pct=-8.0)
        assert signal.action == Action.BUY
        assert signal.ticker == "005930"
        assert signal.strategy_name == "short_overheating"
        assert signal.confidence >= 60

    def test_confidence_increases_with_overheating_days(self):
        strategy = ShortOverheatingStrategy(_make_config(), session=MagicMock())
        sig_short = strategy._build_signal(_make_record(overheating_days=1), drop_pct=0.0)
        sig_long = strategy._build_signal(_make_record(overheating_days=5), drop_pct=0.0)
        assert sig_long.confidence > sig_short.confidence

    def test_size_hint_from_confidence(self):
        strategy = ShortOverheatingStrategy(_make_config(), session=MagicMock())
        sig = strategy._build_signal(_make_record(overheating_days=7), drop_pct=-15.0)
        assert sig.size_hint in (SizeHint.M, SizeHint.L)

    def test_metadata_fields(self):
        strategy = ShortOverheatingStrategy(_make_config(), session=MagicMock())
        sig = strategy._build_signal(_make_record(), drop_pct=-5.0)
        assert "overheating_days" in sig.metadata
        assert "release_date" in sig.metadata
        assert sig.metadata["drop_pct"] == -5.0


# ── _is_entry_today ─────────────────────────────────────


class TestIsEntryToday:
    def test_matches_d2_entry_date(self):
        strategy = ShortOverheatingStrategy(_make_config(d_offset=2), session=MagicMock())
        record = _make_record(release_date=date(2026, 3, 25))  # Wed
        assert strategy._is_entry_today(record, date(2026, 3, 27)) is True  # Fri = D+2

    def test_rejects_wrong_date(self):
        strategy = ShortOverheatingStrategy(_make_config(d_offset=2), session=MagicMock())
        record = _make_record(release_date=date(2026, 3, 25))
        assert strategy._is_entry_today(record, date(2026, 3, 26)) is False  # D+1

    def test_skips_already_signaled(self):
        strategy = ShortOverheatingStrategy(_make_config(d_offset=2), session=MagicMock())
        record = _make_record(release_date=date(2026, 3, 25))
        strategy._signaled.add("005930_20260325")
        assert strategy._is_entry_today(record, date(2026, 3, 27)) is False


# ── _poll_once ──────────────────────────────────────────


class TestPollOnce:
    @pytest.mark.asyncio
    async def test_poll_generates_signal_on_entry_day(self):
        config = _make_config(d_offset=2, lookback_days=7)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())

        record = _make_record(release_date=date(2026, 3, 25), overheating_days=3)
        mock_today = date(2026, 3, 27)  # Fri = D+2

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals = await strategy._poll_once()

        assert len(signals) == 1
        assert signals[0].ticker == "005930"
        assert signals[0].action == Action.BUY

    @pytest.mark.asyncio
    async def test_poll_no_signal_on_wrong_day(self):
        config = _make_config(d_offset=2, lookback_days=7)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())

        record = _make_record(release_date=date(2026, 3, 25), overheating_days=3)
        mock_today = date(2026, 3, 26)  # D+1

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals = await strategy._poll_once()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_duplicate_signals(self):
        config = _make_config(d_offset=2, lookback_days=7)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())

        record = _make_record(release_date=date(2026, 3, 25), overheating_days=3)
        mock_today = date(2026, 3, 27)

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals1 = await strategy._poll_once()
            signals2 = await strategy._poll_once()

        assert len(signals1) == 1
        assert len(signals2) == 0  # 중복 차단

    @pytest.mark.asyncio
    async def test_poll_empty_fetch(self):
        config = _make_config()
        strategy = ShortOverheatingStrategy(config, session=MagicMock())

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = []
            mock_now = MagicMock()
            mock_now.date.return_value = date(2026, 3, 27)
            mock_dt.now.return_value = mock_now

            signals = await strategy._poll_once()

        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_min_overheating_days_filter(self):
        config = _make_config(d_offset=2, min_days=3)
        strategy = ShortOverheatingStrategy(config, session=MagicMock())

        # 1일 과열 → min_days=3 미달로 스킵
        record = _make_record(release_date=date(2026, 3, 25), overheating_days=1)
        mock_today = date(2026, 3, 27)

        with patch("kindshot.short_overheating_strategy.fetch_overheating_records") as mock_fetch, \
             patch("kindshot.short_overheating_strategy.datetime") as mock_dt:
            mock_fetch.return_value = [record]
            mock_now = MagicMock()
            mock_now.date.return_value = mock_today
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            signals = await strategy._poll_once()

        assert len(signals) == 0
