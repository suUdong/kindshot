"""Tests for main pipeline branching: duplicate, LLM failure, guardrail."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.bucket import BucketResult
from kindshot.config import Config
from kindshot.context_card import ContextCardData
from kindshot.decision import LlmTimeoutError, LlmParseError, LlmCallError
from kindshot.kis_client import OrderbookSnapshot, QuoteRiskState
from kindshot.models import Bucket, SkipStage


async def _run_pipeline_once(
    tmp_path,
    raw_items,
    decision_side_effect=None,
    guardrail_passed=True,
    dry_run=False,
    paper=False,
    ctx_raw=None,
):
    """Helper: run one iteration of the pipeline and return logged records."""
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(log_dir=tmp_path / "logs", dry_run=dry_run, paper=paper)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    # Simulate initialized market for pipeline tests
    market._initialized = True
    market._halted = False
    fetcher = PriceFetcher(kis=None)
    scheduler = SnapshotScheduler(cfg, fetcher, log)

    mock_engine = MagicMock()
    if decision_side_effect:
        mock_engine.decide = AsyncMock(side_effect=decision_side_effect)
    else:
        mock_engine.decide = AsyncMock(return_value=None)

    # Import pipeline function
    from kindshot.main import _pipeline_loop
    from kindshot.feed import KindFeed

    # Create a mock feed that yields once then stops
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield raw_items
    mock_feed.stream = _one_batch

    # Run pipeline with a timeout
    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.main.check_guardrails") as mock_gr:
        from kindshot.models import ContextCard
        from kindshot.guardrails import GuardrailResult
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ctx_raw or ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )
        mock_gr.return_value = GuardrailResult(passed=guardrail_passed, reason="BLOCKED" if not guardrail_passed else None)

        mode = "dry_run" if dry_run else ("paper" if paper else "live")
        try:
            await asyncio.wait_for(
                _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode=mode),
                timeout=1.0,
            )
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass

    # Read back logged records
    records = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                records.append(json.loads(line))
    return records


def _make_raw(title="삼성전자(005930) - 공급계약 체결", link="https://kind.krx.co.kr/?rcpNo=20260305000001"):
    from kindshot.feed import RawDisclosure
    return RawDisclosure(
        title=title,
        link=link,
        rss_guid="guid1",
        published="2026-03-05T09:12:04+09:00",
        ticker="005930",
        corp_name="삼성전자",
        detected_at=datetime.now(timezone.utc),
    )


async def test_duplicate_logged_with_skip_stage(tmp_path):
    """Duplicate events should be logged with skip_stage=DUPLICATE."""
    raw = _make_raw()
    # Send same item twice → second is duplicate
    records = await _run_pipeline_once(tmp_path, [raw, raw], dry_run=True)

    dup_records = [r for r in records if r.get("skip_stage") == "DUPLICATE"]
    assert len(dup_records) == 1
    assert dup_records[0]["skip_reason"] == "DUPLICATE"


async def test_llm_timeout_logged(tmp_path):
    """LLM timeout should produce event with skip_stage=LLM_TIMEOUT."""
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=LlmTimeoutError("timeout"),
    )

    timeout_records = [r for r in records if r.get("skip_stage") == "LLM_TIMEOUT"]
    assert len(timeout_records) == 1

    # Verify no double-write: only 1 event record total for this event_id
    event_records = [r for r in records if r.get("type") == "event" and r.get("skip_stage") != "DUPLICATE"]
    assert len(event_records) == 1


async def test_llm_parse_error_logged(tmp_path):
    """LLM parse failure should produce event with skip_stage=LLM_PARSE."""
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=LlmParseError("bad json"),
    )

    parse_records = [r for r in records if r.get("skip_stage") == "LLM_PARSE"]
    assert len(parse_records) == 1


async def test_llm_call_error_logged(tmp_path):
    """LLM call failure should produce event with skip_stage=LLM_ERROR."""
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=LlmCallError("network error"),
    )

    call_error_records = [r for r in records if r.get("skip_stage") == "LLM_ERROR"]
    assert len(call_error_records) == 1


async def test_guardrail_block_logged(tmp_path):
    """Guardrail failure should produce event with skip_stage=GUARDRAIL."""
    from kindshot.models import DecisionRecord, Action, SizeHint
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=[mock_decision],
        guardrail_passed=False,
    )

    gr_records = [r for r in records if r.get("skip_stage") == "GUARDRAIL"]
    assert len(gr_records) == 1
    assert gr_records[0]["skip_reason"] == "BLOCKED"

    # No decision record should be written when guardrail blocks
    decision_records = [r for r in records if r.get("type") == "decision"]
    assert len(decision_records) == 0


def test_runtime_counters_helpers():
    """Runtime counter helpers should aggregate skip stats consistently."""
    from kindshot.main import RuntimeCounters, _counter_snapshot, _mark_skip

    counters = RuntimeCounters()
    _mark_skip(counters, stage="QUANT", reason="RET_TODAY_DATA_MISSING")
    _mark_skip(counters, stage="LLM_ERROR", reason="LLM_ERROR")

    snap = _counter_snapshot(counters)
    assert snap["totals"]["events_skipped"] == 2
    assert snap["skip_stage"]["QUANT"] == 1
    assert snap["skip_stage"]["LLM_ERROR"] == 1
    assert snap["skip_reason"]["RET_TODAY_DATA_MISSING"] == 1
    assert snap["skip_reason"]["LLM_ERROR"] == 1


async def test_paper_mode_logs_decision_with_paper_mode(tmp_path):
    """Paper mode should log event+decision with mode='paper' and no order execution."""
    from kindshot.models import DecisionRecord, Action, SizeHint
    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    records = await _run_pipeline_once(
        tmp_path, [raw],
        decision_side_effect=[mock_decision],
        paper=True,
    )

    event_records = [r for r in records if r.get("type") == "event" and r.get("skip_stage") is None]
    decision_records = [r for r in records if r.get("type") == "decision"]

    assert len(event_records) == 1
    assert event_records[0]["mode"] == "paper"
    assert len(decision_records) == 1
    assert decision_records[0]["mode"] == "paper"


async def test_pipeline_passes_quote_risk_state_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    risk_state = QuoteRiskState(temp_stop_yn="Y", vi_cls_code="D")

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.main import _pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.main.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, quote_risk_state=risk_state),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["quote_risk_state"] == risk_state


async def test_pipeline_passes_orderbook_snapshot_and_action_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()
    orderbook = OrderbookSnapshot(
        ask_price1=50_100.0,
        bid_price1=49_900.0,
        ask_size1=90,
        bid_size1=120,
        total_ask_size=2000,
        total_bid_size=2400,
        spread_bps=40.0,
    )

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.main import _pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.main.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, orderbook_snapshot=orderbook),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["orderbook_snapshot"] == orderbook
    assert mock_gr.call_args.kwargs["decision_action"] == Action.BUY


async def test_pipeline_passes_intraday_value_ratio_to_guardrails(tmp_path):
    from kindshot.models import DecisionRecord, Action, SizeHint

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    raw = _make_raw()

    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.main import _pipeline_loop
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.models import ContextCard

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.main.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, intraday_value_vs_adv20d=0.005),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0, intraday_value_vs_adv20d=0.005),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["intraday_value_vs_adv20d"] == 0.005
    assert mock_gr.call_args.kwargs["decision_action"] == Action.BUY


async def test_market_breadth_risk_off_blocks_before_llm(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.models import MarketContext
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.main import _pipeline_loop
    from kindshot.feed import KindFeed

    cfg = Config(log_dir=tmp_path / "logs", paper=True, min_market_breadth_ratio=0.8)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    market._kospi_change = -0.2
    market._kosdaq_change = -0.3
    market._kospi_breadth_ratio = 0.5
    market._kosdaq_breadth_ratio = 0.6
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock()

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [_make_raw()]
    mock_feed.stream = _one_batch

    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.main.check_guardrails") as mock_gr:
        from kindshot.models import ContextCard
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )

        await asyncio.wait_for(
            _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    mock_engine.decide.assert_not_awaited()
    mock_gr.assert_not_called()

    records = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                records.append(json.loads(line))

    blocked = [r for r in records if r.get("skip_reason") == "MARKET_BREADTH_RISK_OFF"]
    assert len(blocked) == 1


async def test_market_halt_block_logged_before_llm(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.logger import JsonlLogger
    from kindshot.market import MarketMonitor
    from kindshot.price import PriceFetcher, SnapshotScheduler
    from kindshot.main import _pipeline_loop
    from kindshot.feed import KindFeed

    cfg = Config(log_dir=tmp_path / "logs", paper=True)
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = True
    market._kospi_change = -8.5
    market._kosdaq_change = -7.2
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock()

    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [_make_raw()]
    mock_feed.stream = _one_batch

    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx:
        from kindshot.models import ContextCard
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0),
            ContextCardData(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
        )

        await asyncio.wait_for(
            _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    mock_engine.decide.assert_not_awaited()

    records = []
    for f in (tmp_path / "logs").glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line:
                records.append(json.loads(line))

    blocked = [r for r in records if r.get("skip_reason") == "MARKET_HALTED"]
    assert len(blocked) == 1
    assert blocked[0]["skip_stage"] == SkipStage.GUARDRAIL.value


async def test_pipeline_persists_runtime_context_card(tmp_path):
    from kindshot.event_registry import EventRegistry
    from kindshot.feed import KindFeed
    from kindshot.guardrails import GuardrailResult
    from kindshot.logger import JsonlLogger
    from kindshot.main import _pipeline_loop
    from kindshot.market import MarketMonitor
    from kindshot.models import ContextCard, DecisionRecord, Action, SizeHint
    from kindshot.price import PriceFetcher, SnapshotScheduler

    cfg = Config(
        log_dir=tmp_path / "logs",
        paper=True,
        runtime_context_cards_dir=tmp_path / "data" / "runtime" / "context_cards",
    )
    log = JsonlLogger(cfg.log_dir, run_id="test_run")
    registry = EventRegistry()
    market = MarketMonitor(cfg)
    market._initialized = True
    market._halted = False
    market._kospi_change = -0.2
    market._kosdaq_change = 0.1
    scheduler = SnapshotScheduler(cfg, PriceFetcher(kis=None), log)

    mock_decision = DecisionRecord(
        schema_version="0.1.2",
        run_id="test_run",
        event_id="",
        decided_at=datetime.now(timezone.utc),
        llm_model="test",
        llm_latency_ms=10,
        action=Action.BUY,
        confidence=80,
        size_hint=SizeHint.M,
        reason="test",
        decision_source="LLM",
    )
    mock_engine = MagicMock()
    mock_engine.decide = AsyncMock(return_value=mock_decision)

    raw = _make_raw()
    mock_feed = AsyncMock(spec=KindFeed)

    async def _one_batch():
        yield [raw]
    mock_feed.stream = _one_batch

    with patch("kindshot.main.build_context_card", new_callable=AsyncMock) as mock_ctx, \
         patch("kindshot.main.check_guardrails") as mock_gr:
        mock_ctx.return_value = (
            ContextCard(adv_value_20d=10e9, spread_bps=10.0, ret_today=5.0),
            ContextCardData(
                adv_value_20d=10e9,
                spread_bps=10.0,
                ret_today=5.0,
                quote_risk_state=QuoteRiskState(temp_stop_yn="Y", vi_cls_code="D"),
                orderbook_snapshot=OrderbookSnapshot(
                    ask_price1=50_100.0,
                    bid_price1=49_900.0,
                    ask_size1=90,
                    bid_size1=120,
                    total_ask_size=2000,
                    total_bid_size=2400,
                    spread_bps=40.0,
                ),
            ),
        )
        mock_gr.return_value = GuardrailResult(passed=True)

        await asyncio.wait_for(
            _pipeline_loop(mock_feed, registry, mock_engine, market, scheduler, log, cfg, "test_run", None, mode="paper"),
            timeout=1.0,
        )

    files = list((tmp_path / "data" / "runtime" / "context_cards").glob("*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "context_card"
    assert rows[0]["ticker"] == "005930"
    assert rows[0]["bucket"] == "POS_STRONG"
    assert rows[0]["quant_check_passed"] is True
    assert rows[0]["ctx"]["ret_today"] == 5.0
    assert rows[0]["raw"]["quote_risk_state"]["vi_cls_code"] == "D"
    assert rows[0]["raw"]["orderbook_snapshot"]["total_bid_size"] == 2400
    mock_engine.decide.assert_awaited_once()


async def test_wait_or_stop_interrupts_timeout():
    """_wait_or_stop should return immediately when stop_event is set."""
    import time
    from kindshot.main import _wait_or_stop

    stop_event = asyncio.Event()
    stop_event.set()
    t0 = time.monotonic()
    await _wait_or_stop(stop_event, 60)
    assert time.monotonic() - t0 < 0.1
