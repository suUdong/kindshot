"""Tests for the multi-strategy framework: Strategy protocol, TradeSignal, StrategyRegistry, NewsStrategy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest

from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource, Strategy, StrategyRegistry, TradeSignal


# ── TradeSignal tests ──────────────────────────────────


class TestTradeSignal:
    def test_basic_creation(self):
        sig = TradeSignal(
            strategy_name="news",
            source=SignalSource.NEWS,
            ticker="005930",
            corp_name="삼성전자",
            action=Action.BUY,
            confidence=75,
            size_hint=SizeHint.M,
            reason="수주 공시",
        )
        assert sig.ticker == "005930"
        assert sig.confidence == 75
        assert sig.source == SignalSource.NEWS

    def test_confidence_validation(self):
        with pytest.raises(ValueError, match="confidence must be 0-100"):
            TradeSignal(
                strategy_name="test",
                source=SignalSource.NEWS,
                ticker="005930",
                corp_name="삼성전자",
                action=Action.BUY,
                confidence=150,
            )

    def test_confidence_negative(self):
        with pytest.raises(ValueError, match="confidence must be 0-100"):
            TradeSignal(
                strategy_name="test",
                source=SignalSource.NEWS,
                ticker="005930",
                corp_name="삼성전자",
                action=Action.SKIP,
                confidence=-1,
            )

    def test_defaults(self):
        sig = TradeSignal(
            strategy_name="test",
            source=SignalSource.TECHNICAL,
            ticker="000660",
            corp_name="SK하이닉스",
            action=Action.BUY,
            confidence=60,
        )
        assert sig.size_hint == SizeHint.S
        assert sig.reason == ""
        assert sig.metadata == {}
        assert sig.event_id == ""

    def test_metadata(self):
        sig = TradeSignal(
            strategy_name="alpha",
            source=SignalSource.ALPHA,
            ticker="035720",
            corp_name="카카오",
            action=Action.BUY,
            confidence=80,
            metadata={"alpha_score": 92, "conviction": "HIGH"},
        )
        assert sig.metadata["alpha_score"] == 92


# ── Signal Source tests ────────────────────────────────


class TestSignalSource:
    def test_all_sources(self):
        sources = [s.value for s in SignalSource]
        assert "NEWS" in sources
        assert "TECHNICAL" in sources
        assert "Y2I" in sources
        assert "ALPHA" in sources
        assert "MACRO" in sources
        assert "COMPOSITE" in sources


# ── Dummy strategy for testing ─────────────────────────


class DummyStrategy:
    """Protocol-compliant dummy strategy for tests."""

    def __init__(self, name: str = "dummy", source: SignalSource = SignalSource.TECHNICAL,
                 enabled: bool = True, signals: list[TradeSignal] | None = None):
        self._name = name
        self._source = source
        self._enabled = enabled
        self._signals = signals or []
        self.started = False
        self.stopped = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def source(self) -> SignalSource:
        return self._source

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def stream_signals(self) -> AsyncIterator[TradeSignal]:
        for sig in self._signals:
            yield sig

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


# ── Strategy Protocol tests ────────────────────────────


class TestStrategyProtocol:
    def test_dummy_is_strategy(self):
        d = DummyStrategy()
        assert isinstance(d, Strategy)

    def test_dummy_properties(self):
        d = DummyStrategy(name="test_strat", source=SignalSource.Y2I, enabled=False)
        assert d.name == "test_strat"
        assert d.source == SignalSource.Y2I
        assert d.enabled is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        d = DummyStrategy()
        await d.start()
        assert d.started
        await d.stop()
        assert d.stopped

    @pytest.mark.asyncio
    async def test_stream_signals(self):
        signals = [
            TradeSignal(
                strategy_name="dummy",
                source=SignalSource.TECHNICAL,
                ticker="005930",
                corp_name="삼성전자",
                action=Action.BUY,
                confidence=70,
            ),
        ]
        d = DummyStrategy(signals=signals)
        collected = [s async for s in d.stream_signals()]
        assert len(collected) == 1
        assert collected[0].ticker == "005930"


# ── StrategyRegistry tests ─────────────────────────────


class TestStrategyRegistry:
    def test_register(self):
        reg = StrategyRegistry()
        s = DummyStrategy(name="s1")
        reg.register(s)
        assert reg.get("s1") is s
        assert len(reg.strategies) == 1

    def test_register_duplicate(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy(name="s1"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(DummyStrategy(name="s1"))

    def test_active_strategies(self):
        reg = StrategyRegistry()
        reg.register(DummyStrategy(name="active", enabled=True))
        reg.register(DummyStrategy(name="inactive", enabled=False))
        assert len(reg.active_strategies) == 1
        assert reg.active_strategies[0].name == "active"

    @pytest.mark.asyncio
    async def test_start_all(self):
        reg = StrategyRegistry()
        s1 = DummyStrategy(name="s1")
        s2 = DummyStrategy(name="s2", enabled=False)
        reg.register(s1)
        reg.register(s2)
        await reg.start_all()
        assert s1.started
        assert not s2.started  # disabled, not started

    @pytest.mark.asyncio
    async def test_stop_all(self):
        reg = StrategyRegistry()
        s1 = DummyStrategy(name="s1")
        s2 = DummyStrategy(name="s2", enabled=False)
        reg.register(s1)
        reg.register(s2)
        await reg.stop_all()
        assert s1.stopped
        assert s2.stopped  # stop_all stops all, even disabled

    @pytest.mark.asyncio
    async def test_stream_all(self):
        sig1 = TradeSignal(
            strategy_name="s1", source=SignalSource.NEWS,
            ticker="005930", corp_name="삼성전자", action=Action.BUY, confidence=80,
        )
        sig2 = TradeSignal(
            strategy_name="s2", source=SignalSource.TECHNICAL,
            ticker="000660", corp_name="SK하이닉스", action=Action.BUY, confidence=65,
        )
        reg = StrategyRegistry()
        reg.register(DummyStrategy(name="s1", source=SignalSource.NEWS, signals=[sig1]))
        reg.register(DummyStrategy(name="s2", source=SignalSource.TECHNICAL, signals=[sig2]))

        collected = []
        async for sig in reg.stream_all():
            collected.append(sig)
        assert len(collected) == 2
        tickers = {s.ticker for s in collected}
        assert tickers == {"005930", "000660"}

    @pytest.mark.asyncio
    async def test_stream_all_empty(self):
        reg = StrategyRegistry()
        collected = [s async for s in reg.stream_all()]
        assert collected == []

    @pytest.mark.asyncio
    async def test_stream_all_one_fails(self):
        """한 전략이 실패해도 다른 전략의 시그널은 수집된다."""

        class FailingStrategy:
            @property
            def name(self) -> str:
                return "failing"

            @property
            def source(self) -> SignalSource:
                return SignalSource.TECHNICAL

            @property
            def enabled(self) -> bool:
                return True

            async def stream_signals(self) -> AsyncIterator[TradeSignal]:
                raise RuntimeError("boom")
                yield  # pragma: no cover

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

        good_sig = TradeSignal(
            strategy_name="good", source=SignalSource.NEWS,
            ticker="005930", corp_name="삼성전자", action=Action.BUY, confidence=70,
        )
        reg = StrategyRegistry()
        reg.register(FailingStrategy())
        reg.register(DummyStrategy(name="good", source=SignalSource.NEWS, signals=[good_sig]))

        collected = [s async for s in reg.stream_all()]
        assert len(collected) == 1
        assert collected[0].ticker == "005930"


# ── NewsStrategy import test ───────────────────────────


class TestNewsStrategyImport:
    def test_import(self):
        from kindshot.news_strategy import NewsStrategy
        assert NewsStrategy is not None

    def test_protocol_compliance(self):
        """NewsStrategy가 Strategy 프로토콜을 만족하는지 확인."""
        from kindshot.news_strategy import NewsStrategy
        # NewsStrategy requires many constructor args; just check class has the right attributes
        assert hasattr(NewsStrategy, "name")
        assert hasattr(NewsStrategy, "source")
        assert hasattr(NewsStrategy, "enabled")
        assert hasattr(NewsStrategy, "stream_signals")
        assert hasattr(NewsStrategy, "start")
        assert hasattr(NewsStrategy, "stop")
        assert hasattr(NewsStrategy, "run_pipeline")
