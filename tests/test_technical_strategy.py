from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from kindshot.config import Config
from kindshot.guardrails import GuardrailState
from kindshot.logger import JsonlLogger
from kindshot.models import Action, ContextCard, MarketContext, SizeHint
from kindshot.strategy import Strategy
from kindshot.strategy_runtime import consume_strategy_signals
from kindshot.technical_strategy import TechnicalStrategy
from kindshot.tz import KST as _KST


@pytest.mark.asyncio
async def test_technical_strategy_is_protocol_compliant():
    strategy = TechnicalStrategy(
        Config(technical_strategy_enabled=True, technical_strategy_tickers=("005930",)),
        kis=object(),
        hist_fetcher=lambda _ticker: _async_result(
            {
                "prev_close": 100.0,
                "avg_volume_20d": 1000.0,
                "rsi_14": 58.0,
                "macd_hist": 1.4,
                "bb_position": 64.0,
                "atr_14": 2.1,
            }
        ),
        mtf_analyzer=lambda _ticker, _kis, _cfg: _async_result(_MtfStub(100, "aligned")),
    )
    assert isinstance(strategy, Strategy)


@pytest.mark.asyncio
async def test_technical_strategy_emits_signal_for_qualifying_snapshot():
    stop_event = asyncio.Event()

    async def stop_after_sleep(_seconds: float) -> None:
        stop_event.set()

    strategy = TechnicalStrategy(
        Config(
            technical_strategy_enabled=True,
            technical_strategy_tickers=("005930",),
            technical_strategy_poll_interval_s=0.01,
        ),
        kis=_PriceClient(px=103.0, cum_volume=800.0),
        stop_event=stop_event,
        hist_fetcher=lambda _ticker: _async_result(
            {
                "prev_close": 100.0,
                "avg_volume_20d": 1000.0,
                "rsi_14": 58.0,
                "macd_hist": 1.4,
                "bb_position": 64.0,
                "atr_14": 2.1,
            }
        ),
        mtf_analyzer=lambda _ticker, _kis, _cfg: _async_result(_MtfStub(100, "aligned")),
        sleep_fn=stop_after_sleep,
    )

    signals = [signal async for signal in strategy.stream_signals()]
    assert len(signals) == 1
    assert signals[0].ticker == "005930"
    assert signals[0].source.value == "TECHNICAL"


@pytest.mark.asyncio
async def test_technical_strategy_skips_non_qualifying_snapshot():
    strategy = TechnicalStrategy(
        Config(
            technical_strategy_enabled=True,
            technical_strategy_tickers=("005930",),
        ),
        kis=_PriceClient(px=99.0, cum_volume=50.0),
        hist_fetcher=lambda _ticker: _async_result(
            {
                "prev_close": 100.0,
                "avg_volume_20d": 1000.0,
                "rsi_14": 78.0,
                "macd_hist": -0.5,
                "bb_position": 91.0,
                "atr_14": 2.1,
            }
        ),
        mtf_analyzer=lambda _ticker, _kis, _cfg: _async_result(_MtfStub(50, "mixed")),
    )

    assert await strategy.scan_once() == []


@pytest.mark.asyncio
async def test_technical_strategy_cooldown_suppresses_duplicate_emission():
    time_points = iter([0.0, 10.0, 20.0])
    strategy = TechnicalStrategy(
        Config(
            technical_strategy_enabled=True,
            technical_strategy_tickers=("005930",),
            technical_strategy_signal_cooldown_s=300.0,
        ),
        kis=_PriceClient(px=103.0, cum_volume=800.0),
        hist_fetcher=lambda _ticker: _async_result(
            {
                "prev_close": 100.0,
                "avg_volume_20d": 1000.0,
                "rsi_14": 58.0,
                "macd_hist": 1.4,
                "bb_position": 64.0,
                "atr_14": 2.1,
            }
        ),
        mtf_analyzer=lambda _ticker, _kis, _cfg: _async_result(_MtfStub(100, "aligned")),
        monotonic_fn=lambda: next(time_points),
    )

    first = await strategy.scan_once()
    second = await strategy.scan_once()
    assert len(first) == 1
    assert second == []


@pytest.mark.asyncio
async def test_consume_strategy_signals_writes_runtime_record(tmp_path):
    config = Config(runtime_index_path=tmp_path / "runtime" / "index.json", log_dir=tmp_path / "logs")
    log = JsonlLogger(config.log_dir, run_id="run_test")
    registry = _SingleSignalRegistry()

    await consume_strategy_signals(registry, log=log, config=config, run_id="run_test", mode="paper")

    lines = log.current_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["type"] == "strategy_signal"
    assert payload["strategy_name"] == "technical"

    runtime_index = json.loads(config.runtime_index_path.read_text(encoding="utf-8"))
    assert runtime_index["entries"][0]["artifacts"]["strategy_signals"]["exists"] is True


@pytest.mark.asyncio
async def test_consume_strategy_signals_executes_buy_signal_in_paper_mode(tmp_path):
    config = Config(
        runtime_index_path=tmp_path / "runtime" / "index.json",
        log_dir=tmp_path / "logs",
        no_buy_after_kst_hour=24,
    )
    log = JsonlLogger(config.log_dir, run_id="run_test")
    scheduler = _SchedulerStub()
    guardrail_state = GuardrailState(config)
    registry = _SingleSignalRegistry(
        signal=_test_signal(detected_at=datetime(2026, 3, 29, 10, 0, tzinfo=_KST))
    )

    await consume_strategy_signals(
        registry,
        log=log,
        config=config,
        run_id="run_test",
        mode="paper",
        execute_signals=True,
        market=_MarketStub(),
        scheduler=scheduler,
        kis=object(),
        guardrail_state=guardrail_state,
        context_builder=_passing_context_builder,
    )

    records = [json.loads(line) for line in log.current_path().read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["strategy_signal", "event", "decision"]
    event = records[1]
    decision = records[2]
    assert event["decision_source"] == "STRATEGY_SIGNAL"
    assert decision["decision_source"] == "STRATEGY_SIGNAL"
    assert scheduler.calls[0]["event_id"] == event["event_id"] == decision["event_id"]
    assert scheduler.calls[0]["is_buy_decision"] is True
    assert guardrail_state.position_count == 1
    assert "005930" in guardrail_state.bought_tickers


@pytest.mark.asyncio
async def test_consume_strategy_signals_blocks_halted_market_without_decision(tmp_path):
    config = Config(
        runtime_index_path=tmp_path / "runtime" / "index.json",
        log_dir=tmp_path / "logs",
        no_buy_after_kst_hour=24,
    )
    log = JsonlLogger(config.log_dir, run_id="run_test")
    scheduler = _SchedulerStub()
    registry = _SingleSignalRegistry(
        signal=_test_signal(detected_at=datetime(2026, 3, 29, 10, 0, tzinfo=_KST))
    )

    await consume_strategy_signals(
        registry,
        log=log,
        config=config,
        run_id="run_test",
        mode="paper",
        execute_signals=True,
        market=_MarketStub(is_halted=True, is_initialized=False),
        scheduler=scheduler,
        kis=object(),
        guardrail_state=GuardrailState(config),
        context_builder=_passing_context_builder,
    )

    records = [json.loads(line) for line in log.current_path().read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == ["strategy_signal", "event"]
    assert records[1]["skip_reason"] == "MARKET_NOT_INITIALIZED"
    assert scheduler.calls == []


@pytest.mark.asyncio
async def test_consume_strategy_signals_attempts_live_order_for_buy_signal(tmp_path):
    config = Config(
        runtime_index_path=tmp_path / "runtime" / "index.json",
        log_dir=tmp_path / "logs",
        no_buy_after_kst_hour=24,
    )
    log = JsonlLogger(config.log_dir, run_id="run_test")
    scheduler = _SchedulerStub()
    order_executor = _OrderExecutorStub()
    guardrail_state = GuardrailState(config, account_balance=20_000_000)
    registry = _SingleSignalRegistry(
        signal=_test_signal(
            confidence=84,
            size_hint=SizeHint.M,
            detected_at=datetime(2026, 3, 29, 10, 5, tzinfo=_KST),
        )
    )

    await consume_strategy_signals(
        registry,
        log=log,
        config=config,
        run_id="run_test",
        mode="live",
        execute_signals=True,
        market=_MarketStub(),
        scheduler=scheduler,
        kis=object(),
        guardrail_state=guardrail_state,
        order_executor=order_executor,
        context_builder=_passing_context_builder,
    )

    assert len(order_executor.calls) == 1
    call = order_executor.calls[0]
    assert call["ticker"] == "005930"
    assert call["current_price"] == 103.0
    assert call["target_won"] > 0
    assert scheduler.calls[0]["is_buy_decision"] is True


async def _async_result(value):
    return value


class _PriceInfo:
    def __init__(self, *, px: float, cum_volume: float, sector: str = "반도체"):
        self.px = px
        self.cum_volume = cum_volume
        self.sector = sector


class _PriceClient:
    def __init__(self, *, px: float, cum_volume: float):
        self._info = _PriceInfo(px=px, cum_volume=cum_volume)

    async def get_price(self, _ticker: str):
        return self._info


class _MtfStub:
    def __init__(self, alignment_score: int, detail: str):
        self.alignment_score = alignment_score
        self.detail = detail


class _SingleSignalRegistry:
    def __init__(self, signal=None):
        self._signal = signal or _test_signal()

    async def stream_all(self):
        from kindshot.strategy import SignalSource, TradeSignal

        signal = self._signal
        if not isinstance(signal, TradeSignal):
            signal = _test_signal()
        yield signal


class _MarketStub:
    def __init__(self, *, is_halted: bool = False, is_initialized: bool = True):
        self.is_halted = is_halted
        self.is_initialized = is_initialized
        self.snapshot = MarketContext(
            kospi_change_pct=0.8,
            kosdaq_change_pct=0.4,
            kospi_breadth_ratio=1.2,
            kosdaq_breadth_ratio=1.1,
            macro_position_multiplier=1.0,
        )


class _SchedulerStub:
    def __init__(self):
        self.calls = []

    def schedule_t0(self, **kwargs):
        self.calls.append(kwargs)


class _OrderExecutorStub:
    def __init__(self):
        self.calls = []

    async def buy_market_with_retry(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(success=True)


async def _passing_context_builder(_ticker: str, _kis, _config: Config):
    return (
        ContextCard(
            ret_today=3.0,
            adv_value_20d=20_000_000_000,
            spread_bps=8.0,
            intraday_value_vs_adv20d=0.2,
            prior_volume_rate=150.0,
            volume_ratio_vs_avg20d=1.5,
            atr_14=2.1,
            support_reference_px=98.0,
        ),
        SimpleNamespace(
            px=103.0,
            adv_value_20d=20_000_000_000,
            spread_bps=8.0,
            ret_today=3.0,
            intraday_value_vs_adv20d=0.2,
            prior_volume_rate=150.0,
            volume_ratio_vs_avg20d=1.5,
            sector="반도체",
            quote_risk_state=None,
            orderbook_snapshot=None,
            support_reference_px=98.0,
        ),
    )


def _test_signal(
    *,
    confidence: int = 79,
    size_hint: SizeHint = SizeHint.M,
    detected_at: datetime | None = None,
):
    from kindshot.strategy import SignalSource, TradeSignal

    return TradeSignal(
        strategy_name="technical",
        source=SignalSource.TECHNICAL,
        ticker="005930",
        corp_name="005930",
        action=Action.BUY,
        confidence=confidence,
        size_hint=size_hint,
        reason="mtf=100 rsi=58 macd=1.4 bb=64 vol=0.8 ret=3.0%",
        detected_at=detected_at or datetime(2026, 3, 29, 10, 0, tzinfo=_KST),
        metadata={"emitted_for_test": True},
    )
