"""Tests for __main__.py and main.py CLI argument parsing and helpers."""

import asyncio
import pytest

from kindshot.config import Config
from kindshot.main import _build_strategy_registry, _parse_args, _run_mode, _wait_or_stop


def test_parse_args_default():
    """Default args should parse without error."""
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot"]
        args = _parse_args()
        assert args.replay is None
        assert args.replay_runtime_date is None
        assert args.replay_day is None
        assert args.paper is False
    finally:
        sys.argv = orig


def test_parse_args_paper_mode():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--paper"]
        args = _parse_args()
        assert args.paper is True
    finally:
        sys.argv = orig


def test_parse_args_replay():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--replay", "logs/test.jsonl"]
        args = _parse_args()
        assert args.replay == "logs/test.jsonl"
    finally:
        sys.argv = orig


def test_parse_args_replay_day():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--replay-day", "20260316"]
        args = _parse_args()
        assert args.replay_day == "20260316"
    finally:
        sys.argv = orig


def test_parse_args_unknown_review_summary():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--unknown-review-summary"]
        args = _parse_args()
        assert args.unknown_review_summary is True
    finally:
        sys.argv = orig


def test_parse_args_replay_ops_cycle():
    import sys
    orig = sys.argv
    try:
        sys.argv = ["kindshot", "--replay-ops-cycle-ready", "--replay-ops-run-limit", "3"]
        args = _parse_args()
        assert args.replay_ops_cycle_ready is True
        assert args.replay_ops_run_limit == 3
    finally:
        sys.argv = orig


def test_run_mode_dry_run():
    cfg = Config(dry_run=True, paper=False)
    assert _run_mode(cfg) == "dry_run"


def test_run_mode_paper():
    cfg = Config(dry_run=False, paper=True)
    assert _run_mode(cfg) == "paper"


def test_run_mode_live():
    cfg = Config(dry_run=False, paper=False)
    assert _run_mode(cfg) == "live"


@pytest.mark.asyncio
async def test_wait_or_stop_times_out():
    """_wait_or_stop returns after timeout when event not set."""
    stop = asyncio.Event()
    await _wait_or_stop(stop, 0.05)
    assert not stop.is_set()


@pytest.mark.asyncio
async def test_wait_or_stop_interrupted_by_event():
    """_wait_or_stop returns immediately when event is set."""
    stop = asyncio.Event()
    stop.set()
    await _wait_or_stop(stop, 10.0)  # would block 10s without event
    assert stop.is_set()


def test_build_strategy_registry_registers_technical_when_enabled(monkeypatch):
    class DummyNewsStrategy:
        def __init__(self, *args, **kwargs):
            self.name = "news"
            self.source = type("Source", (), {"value": "NEWS"})()
            self.enabled = True

        async def start(self):
            return None

        async def stop(self):
            return None

        async def stream_signals(self):
            if False:
                yield None

    class DummyTechnicalStrategy:
        def __init__(self, *args, **kwargs):
            self.name = "technical"
            self.source = type("Source", (), {"value": "TECHNICAL"})()
            self.enabled = True

        async def start(self):
            return None

        async def stop(self):
            return None

        async def stream_signals(self):
            if False:
                yield None

    import kindshot.main as mod

    monkeypatch.setattr(mod, "NewsStrategy", DummyNewsStrategy)
    monkeypatch.setattr(mod, "TechnicalStrategy", DummyTechnicalStrategy)

    registry, _news, has_signal_strategies = _build_strategy_registry(
        Config(
            technical_strategy_enabled=True,
            technical_strategy_tickers=("005930",),
        ),
        feed=object(),
        registry=object(),
        decision_engine=object(),
        market=object(),
        scheduler=object(),
        log=object(),
        run_id="run_test",
        kis=object(),
        counters=None,
        mode="paper",
        stop_event=asyncio.Event(),
        guardrail_state=object(),
        feed_source="KIS",
        unknown_review_queue=None,
        health_state=None,
        order_executor=None,
        recent_pattern_profile=None,
    )

    assert registry.get("news") is not None
    assert registry.get("technical") is not None
    assert has_signal_strategies is True
