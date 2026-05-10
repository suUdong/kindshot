"""Tests for AlphaFeed: alpha-scanner AlphaFeed payload -> TradeSignal."""

from __future__ import annotations

from datetime import datetime

import pytest

from kindshot.config import Config
from kindshot.feed import AlphaFeed
from kindshot.models import Action, SizeHint
from kindshot.strategy import SignalSource


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _FakeResponse(self.payload, self.status)


def _payload(*signals: dict) -> dict:
    return {
        "status": "ok",
        "source": "alpha-scanner",
        "feed": "kindshot-alpha-feed",
        "signals": list(signals),
    }


def _signal(**overrides) -> dict:
    base = {
        "signal_id": 101,
        "ticker": "005930",
        "corp_name": "Samsung Electronics",
        "market": "KR",
        "sector": "Technology",
        "signal_type": "STRONG_BUY",
        "action": "BUY",
        "confidence": 88,
        "size_hint": "L",
        "score_current": 84.0,
        "score_previous": 74.0,
        "score_delta": 10.0,
        "regime": "expansion",
        "reason": "fresh alpha conviction",
        "created_at": "2026-05-11T09:10:00+09:00",
        "age_hours": 1.0,
        "fp_filter_status": "emitted",
        "support_count": 4,
        "secondary_support_count": 2,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_alpha_feed_converts_qualified_payload_to_trade_signal():
    config = Config(
        alpha_feed_enabled=True,
        alpha_scanner_api_base_url="http://alpha.local",
        alpha_feed_min_confidence=78,
        alpha_feed_lookback_days=5,
        alpha_feed_limit=10,
    )
    session = _FakeSession(
        _payload(
            _signal(),
            _signal(signal_id=102, ticker="AVGO", confidence=95),
            _signal(signal_id=103, ticker="000660", confidence=77),
        )
    )
    feed = AlphaFeed(config, session)  # type: ignore[arg-type]

    results = await feed.poll_once()

    assert len(results) == 1
    signal = results[0]
    assert signal.strategy_name == "alpha_feed"
    assert signal.source == SignalSource.ALPHA
    assert signal.action == Action.BUY
    assert signal.ticker == "005930"
    assert signal.size_hint == SizeHint.L
    assert signal.confidence == 88
    assert signal.event_id == "alpha_101"
    assert signal.detected_at == datetime.fromisoformat("2026-05-11T09:10:00+09:00")
    assert signal.metadata["score_delta"] == 10.0
    assert session.calls[0]["url"] == "http://alpha.local/kindshot/alpha-feed"
    assert session.calls[0]["params"]["min_confidence"] == 78


@pytest.mark.asyncio
async def test_alpha_feed_dedups_seen_signal_ids():
    config = Config(alpha_feed_enabled=True, alpha_scanner_api_base_url="http://alpha.local")
    feed = AlphaFeed(config, _FakeSession(_payload(_signal())))  # type: ignore[arg-type]

    assert len(await feed.poll_once()) == 1
    assert await feed.poll_once() == []


@pytest.mark.asyncio
async def test_alpha_feed_disabled_without_base_url():
    config = Config(alpha_feed_enabled=True, alpha_scanner_api_base_url="")
    feed = AlphaFeed(config, _FakeSession(_payload(_signal())))  # type: ignore[arg-type]

    assert feed.enabled is False
    assert await feed.poll_once() == []
