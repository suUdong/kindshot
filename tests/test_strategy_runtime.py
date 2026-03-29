"""Tests for strategy_runtime helper functions."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace

import pytest

from kindshot.models import Action, SizeHint, SkipStage
from kindshot.strategy import SignalSource, TradeSignal
from kindshot.strategy_runtime import (
    _mark_skip,
    _resolve_detected_at,
    _resolve_event_id,
    _strategy_headline,
)
from kindshot.tz import KST as _KST


# ── _resolve_detected_at ──────────────────────────────


class TestResolveDetectedAt:
    def test_none_returns_now_kst(self) -> None:
        result = _resolve_detected_at(None)
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 9 * 3600  # KST = UTC+9

    def test_naive_gets_kst_attached(self) -> None:
        naive = datetime(2026, 3, 29, 10, 0, 0)
        result = _resolve_detected_at(naive)
        assert result.tzinfo == _KST
        assert result.hour == 10

    def test_aware_converts_to_kst(self) -> None:
        from datetime import timezone

        utc_dt = datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc)
        result = _resolve_detected_at(utc_dt)
        assert result.hour == 10  # UTC+9


# ── _resolve_event_id ─────────────────────────────────


def _make_signal(**kwargs) -> TradeSignal:
    defaults = dict(
        strategy_name="technical",
        source=SignalSource.TECHNICAL,
        ticker="005930",
        corp_name="삼성전자",
        action=Action.BUY,
        confidence=75,
        size_hint=SizeHint.S,
        reason="test reason",
    )
    defaults.update(kwargs)
    return TradeSignal(**defaults)


class TestResolveEventId:
    def test_uses_signal_event_id_when_present(self) -> None:
        signal = _make_signal(event_id="custom_id_123")
        result = _resolve_event_id(signal, datetime(2026, 3, 29, 10, 0, tzinfo=_KST))
        assert result == "custom_id_123"

    def test_generates_deterministic_hash_when_no_event_id(self) -> None:
        signal = _make_signal(event_id="")
        dt = datetime(2026, 3, 29, 10, 0, tzinfo=_KST)
        result1 = _resolve_event_id(signal, dt)
        result2 = _resolve_event_id(signal, dt)
        assert result1 == result2
        assert result1.startswith("st_")
        assert len(result1) == 19  # "st_" + 16 hex chars

    def test_different_inputs_produce_different_ids(self) -> None:
        dt = datetime(2026, 3, 29, 10, 0, tzinfo=_KST)
        id1 = _resolve_event_id(_make_signal(ticker="005930"), dt)
        id2 = _resolve_event_id(_make_signal(ticker="000660"), dt)
        assert id1 != id2


# ── _strategy_headline ────────────────────────────────


class TestStrategyHeadline:
    def test_uses_headline_when_present(self) -> None:
        signal = _make_signal(headline="삼성전자 대규모 수주")
        assert _strategy_headline(signal) == "삼성전자 대규모 수주"

    def test_strips_whitespace(self) -> None:
        signal = _make_signal(headline="  삼성전자 수주  ")
        assert _strategy_headline(signal) == "삼성전자 수주"

    def test_generates_fallback_when_empty(self) -> None:
        signal = _make_signal(headline="")
        result = _strategy_headline(signal)
        assert "TECHNICAL" in result
        assert "technical" in result
        assert "BUY" in result


# ── _mark_skip ────────────────────────────────────────


class TestMarkSkip:
    def test_none_counters_is_noop(self) -> None:
        _mark_skip(None, "MARKET_HALTED")  # should not raise

    def test_counters_without_totals_is_noop(self) -> None:
        _mark_skip(object(), "MARKET_HALTED")  # should not raise

    def test_increments_counters(self) -> None:
        counters = SimpleNamespace(
            totals=defaultdict(int),
            skip_stage=defaultdict(int),
            skip_reason=defaultdict(int),
        )
        _mark_skip(counters, "SPREAD_TOO_WIDE")
        assert counters.totals["events_skipped"] == 1
        assert counters.skip_stage[SkipStage.GUARDRAIL.value] == 1
        assert counters.skip_reason["SPREAD_TOO_WIDE"] == 1

    def test_accumulates_multiple_skips(self) -> None:
        counters = SimpleNamespace(
            totals=defaultdict(int),
            skip_stage=defaultdict(int),
            skip_reason=defaultdict(int),
        )
        _mark_skip(counters, "SPREAD_TOO_WIDE")
        _mark_skip(counters, "LOW_ADV")
        assert counters.totals["events_skipped"] == 2
        assert counters.skip_reason["SPREAD_TOO_WIDE"] == 1
        assert counters.skip_reason["LOW_ADV"] == 1
