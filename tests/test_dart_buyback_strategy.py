"""Tests for dart_buyback_strategy: 시그널 생성, confidence 스코어링, 공시 감지."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kindshot.dart_buyback_strategy import (
    DartBuybackStrategy,
    is_buyback_disclosure,
    score_buyback,
    size_hint_from_confidence,
)
from kindshot.dart_enricher import BuybackInfo
from kindshot.feed import RawDisclosure
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource


# ── is_buyback_disclosure ────────────────────────────────


class TestIsBuybackDisclosure:
    def test_exact_match(self):
        assert is_buyback_disclosure("자기주식취득결정") is True

    def test_wrapped_in_report(self):
        assert is_buyback_disclosure("주요사항보고서(자기주식취득결정)") is True

    def test_with_spaces(self):
        assert is_buyback_disclosure("자기주식 취득 결정") is True

    def test_buyback_short(self):
        assert is_buyback_disclosure("자사주취득") is True

    def test_unrelated(self):
        assert is_buyback_disclosure("주요사항보고서(수주공시)") is False

    def test_empty(self):
        assert is_buyback_disclosure("") is False

    def test_partial_match(self):
        assert is_buyback_disclosure("자사주취득 결정 보고서") is True


# ── score_buyback ────────────────────────────────────────


def _make_config(**overrides):
    config = MagicMock()
    config.dart_buyback_base_confidence = overrides.get("base", 65)
    config.dart_buyback_direct_bonus = overrides.get("direct_bonus", 15)
    config.dart_buyback_trust_bonus = overrides.get("trust_bonus", 8)
    config.dart_buyback_min_amount = overrides.get("min_amount", 1_000_000_000)
    return config


def _make_buyback_info(*, is_direct=True, planned_amount=50_000_000_000):
    return BuybackInfo(
        corp_code="00126380",
        corp_name="삼성전자",
        ticker="005930",
        rcept_no="20260329000001",
        method="직접취득" if is_direct else "신탁계약체결",
        is_direct=is_direct,
        planned_shares=100000,
        planned_amount=planned_amount,
        purpose="주가안정",
        period_start="2026.03.29",
        period_end="2026.06.29",
    )


class TestScoreBuyback:
    def test_direct_large(self):
        """직접매입 + 500억+ → 65 + 15 + 10 = 90."""
        config = _make_config()
        info = _make_buyback_info(is_direct=True, planned_amount=50_000_000_000)
        assert score_buyback(info, config) == 90

    def test_direct_medium(self):
        """직접매입 + 100억 → 65 + 15 + 5 = 85."""
        config = _make_config()
        info = _make_buyback_info(is_direct=True, planned_amount=10_000_000_000)
        assert score_buyback(info, config) == 85

    def test_direct_small(self):
        """직접매입 + 소규모 → 65 + 15 + 0 = 80."""
        config = _make_config()
        info = _make_buyback_info(is_direct=True, planned_amount=5_000_000_000)
        assert score_buyback(info, config) == 80

    def test_trust_large(self):
        """신탁매입 + 500억+ → 65 + 8 + 10 = 83."""
        config = _make_config()
        info = _make_buyback_info(is_direct=False, planned_amount=50_000_000_000)
        assert score_buyback(info, config) == 83

    def test_trust_small(self):
        """신탁매입 + 소규모 → 65 + 8 + 0 = 73."""
        config = _make_config()
        info = _make_buyback_info(is_direct=False, planned_amount=5_000_000_000)
        assert score_buyback(info, config) == 73

    def test_max_cap(self):
        """스코어 100 초과 방지."""
        config = _make_config(base=90, direct_bonus=15)
        info = _make_buyback_info(is_direct=True, planned_amount=50_000_000_000)
        assert score_buyback(info, config) == 100


# ── size_hint_from_confidence ────────────────────────────


class TestSizeHint:
    def test_large(self):
        assert size_hint_from_confidence(90) == SizeHint.L

    def test_medium(self):
        assert size_hint_from_confidence(80) == SizeHint.M

    def test_small(self):
        assert size_hint_from_confidence(65) == SizeHint.S

    def test_boundary_85(self):
        assert size_hint_from_confidence(85) == SizeHint.L

    def test_boundary_75(self):
        assert size_hint_from_confidence(75) == SizeHint.M


# ── DartBuybackStrategy ─────────────────────────────────


class TestDartBuybackStrategy:
    def _make_strategy(self, *, enabled=True):
        config = _make_config()
        config.dart_buyback_enabled = enabled
        config.dart_api_key = "test_key"
        config.dart_base_url = "https://opendart.fss.or.kr/api"
        config.data_dir = Path("/tmp")

        session = MagicMock()
        queue = asyncio.Queue()
        stop_event = asyncio.Event()
        strategy = DartBuybackStrategy(config, session, queue, stop_event=stop_event)
        return strategy, queue, stop_event

    def test_protocol_properties(self):
        strategy, _, _ = self._make_strategy()
        assert strategy.name == "dart_buyback"
        assert strategy.source == SignalSource.NEWS
        assert strategy.enabled is True

    def test_disabled(self):
        strategy, _, _ = self._make_strategy(enabled=False)
        assert strategy.enabled is False

    @pytest.mark.asyncio
    async def test_process_disclosure_with_enricher(self):
        strategy, queue, stop = self._make_strategy()

        info = _make_buyback_info(is_direct=True, planned_amount=50_000_000_000)
        strategy._enricher.fetch_buyback = AsyncMock(return_value=info)

        disc = RawDisclosure(
            title="삼성전자(005930) 주요사항보고서(자기주식취득결정)",
            link="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260329000001",
            rss_guid="20260329000001",
            published="20260329",
            ticker="005930",
            corp_name="삼성전자",
            detected_at=datetime.now(timezone.utc),
        )

        signal = await strategy._process_disclosure(disc)
        assert signal is not None
        assert signal.strategy_name == "dart_buyback"
        assert signal.ticker == "005930"
        assert signal.action == Action.BUY
        assert signal.confidence == 90  # 65 + 15 + 10
        assert signal.size_hint == SizeHint.L
        assert "직접매입" in signal.reason
        assert "500억" in signal.reason
        assert signal.event_id == "buyback_20260329000001"
        assert "buyback" in signal.metadata

    @pytest.mark.asyncio
    async def test_process_disclosure_enricher_fails(self):
        """DS005 조회 실패 시 기본 시그널 생성."""
        strategy, queue, stop = self._make_strategy()
        strategy._enricher.fetch_buyback = AsyncMock(return_value=None)

        disc = RawDisclosure(
            title="현대차(005380) 주요사항보고서(자기주식취득결정)",
            link="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260329000002",
            rss_guid="20260329000002",
            published="20260329",
            ticker="005380",
            corp_name="현대차",
            detected_at=datetime.now(timezone.utc),
        )

        signal = await strategy._process_disclosure(disc)
        assert signal is not None
        assert signal.confidence == 65  # base only
        assert signal.size_hint == SizeHint.S
        assert "상세 미조회" in signal.reason

    @pytest.mark.asyncio
    async def test_process_disclosure_below_min_amount(self):
        """최소 금액 미달 시 스킵."""
        strategy, queue, stop = self._make_strategy()

        info = _make_buyback_info(is_direct=True, planned_amount=500_000_000)  # 5억 < 10억
        strategy._enricher.fetch_buyback = AsyncMock(return_value=info)

        disc = RawDisclosure(
            title="테스트(999999) 자기주식취득결정",
            link="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260329000003",
            rss_guid="20260329000003",
            published="20260329",
            ticker="999999",
            corp_name="테스트",
            detected_at=datetime.now(timezone.utc),
        )

        signal = await strategy._process_disclosure(disc)
        assert signal is None

    @pytest.mark.asyncio
    async def test_stream_signals_dedup(self):
        """동일 rcept_no 중복 처리 방지."""
        strategy, queue, stop = self._make_strategy()

        info = _make_buyback_info()
        strategy._enricher.fetch_buyback = AsyncMock(return_value=info)

        disc = RawDisclosure(
            title="삼성전자(005930) 자기주식취득결정",
            link="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260329000001",
            rss_guid="20260329000001",
            published="20260329",
            ticker="005930",
            corp_name="삼성전자",
            detected_at=datetime.now(timezone.utc),
        )

        # 같은 공시 2번 넣기
        await queue.put(disc)
        await queue.put(disc)
        await queue.put(None)  # sentinel

        signals = []
        async for sig in strategy.stream_signals():
            signals.append(sig)

        assert len(signals) == 1
