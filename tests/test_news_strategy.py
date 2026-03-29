"""Tests for NewsStrategy: Strategy protocol compliance and properties."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kindshot.config import Config
from kindshot.news_strategy import NewsStrategy
from kindshot.strategy import SignalSource, Strategy


def _make_news_strategy(**overrides) -> NewsStrategy:
    defaults = dict(
        config=Config(),
        feed=object(),
        registry=object(),
        decision_engine=object(),
        market=object(),
        scheduler=object(),
        log=object(),
        run_id="test-run",
        kis=None,
        counters=None,
        mode="paper",
    )
    defaults.update(overrides)
    return NewsStrategy(**defaults)


class TestNewsStrategyProtocol:
    def test_is_protocol_compliant(self) -> None:
        strategy = _make_news_strategy()
        assert isinstance(strategy, Strategy)

    def test_name(self) -> None:
        strategy = _make_news_strategy()
        assert strategy.name == "news"

    def test_source(self) -> None:
        strategy = _make_news_strategy()
        assert strategy.source == SignalSource.NEWS

    def test_enabled_default(self) -> None:
        strategy = _make_news_strategy()
        assert strategy.enabled is True


class TestNewsStrategyLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        strategy = _make_news_strategy()
        await strategy.start()
        await strategy.stop()

    @pytest.mark.asyncio
    async def test_stream_signals_is_empty_generator(self) -> None:
        strategy = _make_news_strategy()
        signals = [s async for s in strategy.stream_signals()]
        assert signals == []


class TestNewsStrategyRunPipeline:
    @pytest.mark.asyncio
    async def test_run_pipeline_delegates_to_pipeline_loop(self) -> None:
        mock_loop = AsyncMock()
        strategy = _make_news_strategy(
            stop_event=asyncio.Event(),
            feed_source="KIND",
        )
        with patch("kindshot.news_strategy.pipeline_loop", mock_loop):
            await strategy.run_pipeline()

        mock_loop.assert_awaited_once()
        call_args = mock_loop.call_args
        # pipeline_loop receives positional args; config is 7th, mode is 11th
        assert call_args[0][6] == strategy._config
        assert call_args[0][10] == "paper"
        assert call_args[1]["feed_source"] == "KIND"
