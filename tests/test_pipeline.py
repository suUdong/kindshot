"""Tests for main pipeline branching: duplicate, LLM failure, guardrail."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.bucket import BucketResult
from kindshot.config import Config
from kindshot.decision import LlmTimeoutError, LlmParseError, LlmCallError
from kindshot.models import Bucket, SkipStage


async def _run_pipeline_once(tmp_path, raw_items, decision_side_effect=None, guardrail_passed=True, dry_run=False, paper=False):
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
        mock_ctx.return_value = (ContextCard(adv_value_20d=10e9), {"adv_value_20d": 10e9, "spread_bps": None, "ret_today": 5.0})
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


async def test_wait_or_stop_interrupts_timeout():
    """_wait_or_stop should return immediately when stop_event is set."""
    import time
    from kindshot.main import _wait_or_stop

    stop_event = asyncio.Event()
    stop_event.set()
    t0 = time.monotonic()
    await _wait_or_stop(stop_event, 60)
    assert time.monotonic() - t0 < 0.1
