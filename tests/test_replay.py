"""Tests for replay mode."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kindshot.config import Config
from kindshot.replay import (
    _load_actionable_events,
    _summarize_returns,
    list_collected_dates,
    list_runtime_dates,
    load_collector_day_bundle,
    load_collected_day_manifest,
    load_runtime_day_artifacts,
    load_runtime_day_manifest,
    replay,
    replay_day,
    replay_ops_cycle_ready,
    replay_ops_queue_ready,
    replay_ops_summary,
    replay_ops_run_ready,
    replay_day_status,
    replay_runtime_date,
)


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    log_file = tmp_path / "test.jsonl"
    with open(log_file, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return log_file


def _make_event(bucket="POS_STRONG", quant_passed=True, skip_stage=None) -> dict:
    return {
        "type": "event",
        "mode": "paper",
        "schema_version": "0.1.2",
        "run_id": "test_run",
        "event_id": "evt_001",
        "event_id_method": "UID",
        "event_group_id": "evt_001",
        "detected_at": "2026-03-05T09:12:04+09:00",
        "disclosed_at": "2026-03-05T09:12:00+09:00",
        "ticker": "005930",
        "corp_name": "삼성전자",
        "headline": "반도체 공급계약 체결",
        "bucket": bucket,
        "quant_check_passed": quant_passed,
        "skip_stage": skip_stage,
        "ctx": {
            "ret_today": 3.5,
            "adv_value_20d": 10e9,
            "spread_bps": 8.0,
        },
    }


def test_load_deduplicates_by_event_id(tmp_path):
    """Duplicate event_ids should be deduplicated."""
    evt = _make_event()
    evt2 = _make_event()  # same event_id "evt_001"
    evt2["headline"] = "다른 공시"
    log_file = _write_events(tmp_path, [evt, evt2])
    result = _load_actionable_events(log_file)
    assert len(result) == 1


def test_load_different_event_ids(tmp_path):
    """Different event_ids should both be loaded."""
    evt1 = _make_event()
    evt2 = _make_event()
    evt2["event_id"] = "evt_002"
    log_file = _write_events(tmp_path, [evt1, evt2])
    result = _load_actionable_events(log_file)
    assert len(result) == 2


def test_load_actionable_events_filters(tmp_path):
    events = [
        _make_event(bucket="POS_STRONG", quant_passed=True),
        _make_event(bucket="NEG_STRONG", quant_passed=True),
        _make_event(bucket="POS_STRONG", quant_passed=False),
        {"type": "decision", "action": "BUY"},
    ]
    log_file = _write_events(tmp_path, events)
    result = _load_actionable_events(log_file)
    assert len(result) == 1
    assert result[0]["ticker"] == "005930"


def test_load_empty_file(tmp_path):
    log_file = tmp_path / "empty.jsonl"
    log_file.write_text("")
    result = _load_actionable_events(log_file)
    assert result == []


def test_summarize_returns_reports_drawdown_and_profit_factor():
    summary = _summarize_returns([10.0, -5.0, 2.0, -1.0])
    assert summary["trade_count"] == 4.0
    assert summary["win_rate_pct"] == 50.0
    assert summary["avg_return_pct"] == pytest.approx(1.5)
    assert summary["best_pct"] == 10.0
    assert summary["worst_pct"] == -5.0
    assert summary["max_drawdown_pct"] == pytest.approx(-5.0, abs=0.2)
    assert summary["profit_factor"] == pytest.approx(2.0)


def test_summarize_returns_handles_all_winners():
    summary = _summarize_returns([1.0, 2.0])
    assert summary["avg_loss_pct"] == 0.0
    assert summary["profit_factor"] == float("inf")


def test_list_collected_dates_reads_manifest_index(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    manifests_dir.mkdir(parents=True)
    (manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-15T00:00:00+09:00",
                "entries": [
                    {"date": "20260310", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(manifests_dir / "20260310.json")},
                    {"date": "20260309", "status": "partial", "has_partial_data": True, "generated_at": "2026-03-15T00:02:00+09:00", "manifest_path": str(manifests_dir / "20260309.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(collector_manifests_dir=manifests_dir)

    assert list_collected_dates(cfg) == ["20260310"]
    assert list_collected_dates(cfg, include_partial=True) == ["20260310", "20260309"]


def test_load_collected_day_manifest_reads_manifest(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    manifests_dir.mkdir(parents=True)
    manifest_path = manifests_dir / "20260310.json"
    manifest_path.write_text(
        json.dumps(
            {
                "date": "20260310",
                "status": "complete",
                "paths": {"news": str(tmp_path / "data" / "collector" / "news" / "20260310.jsonl")},
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(collector_manifests_dir=manifests_dir)

    payload = load_collected_day_manifest(cfg, "20260310")

    assert payload["date"] == "20260310"
    assert payload["status"] == "complete"


def test_load_collected_day_manifest_prefers_index_manifest_path(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    relocated_dir = tmp_path / "data" / "collector" / "relocated"
    manifests_dir.mkdir(parents=True)
    relocated_dir.mkdir(parents=True)
    manifest_path = relocated_dir / "custom-20260310.json"
    manifest_path.write_text(
        json.dumps(
            {
                "date": "20260310",
                "status": "partial",
                "status_reason": "daily_index_missing",
                "paths": {"news": str(tmp_path / "data" / "collector" / "news" / "20260310.jsonl")},
            }
        ),
        encoding="utf-8",
    )
    (manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-27T09:00:00+09:00",
                "entries": [
                    {
                        "date": "20260310",
                        "status": "partial",
                        "has_partial_data": True,
                        "generated_at": "2026-03-27T09:00:00+09:00",
                        "manifest_path": str(manifest_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(collector_manifests_dir=manifests_dir)

    payload = load_collected_day_manifest(cfg, "20260310")

    assert payload["date"] == "20260310"
    assert payload["status"] == "partial"
    assert payload["status_reason"] == "daily_index_missing"


def test_load_collected_day_manifest_falls_back_without_index_entry(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    manifests_dir.mkdir(parents=True)
    manifest_path = manifests_dir / "20260310.json"
    manifest_path.write_text(
        json.dumps(
            {
                "date": "20260310",
                "status": "complete",
                "paths": {"news": str(tmp_path / "data" / "collector" / "news" / "20260310.jsonl")},
            }
        ),
        encoding="utf-8",
    )
    (manifests_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-27T09:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    cfg = Config(collector_manifests_dir=manifests_dir)

    payload = load_collected_day_manifest(cfg, "20260310")

    assert payload["date"] == "20260310"
    assert payload["status"] == "complete"


def test_load_collector_day_bundle_reads_manifest_paths(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    news_dir = tmp_path / "data" / "collector" / "news"
    cls_dir = tmp_path / "data" / "collector" / "classifications"
    manifests_dir.mkdir(parents=True)
    news_dir.mkdir(parents=True)
    cls_dir.mkdir(parents=True)
    (news_dir / "20260310.jsonl").write_text(
        json.dumps({"news_id": "n1", "title": "반도체 공급계약 체결", "tickers": ["005930"], "dorg": "삼성전자"}) + "\n",
        encoding="utf-8",
    )
    (cls_dir / "20260310.jsonl").write_text(
        json.dumps({"news_id": "n1", "bucket": "POS_STRONG", "title": "반도체 공급계약 체결", "tickers": ["005930"]}) + "\n",
        encoding="utf-8",
    )
    (manifests_dir / "20260310.json").write_text(
        json.dumps(
            {
                "date": "20260310",
                "status": "complete",
                "counts": {"news": 1, "classifications": 1, "daily_prices": 0, "daily_index": 0},
                "paths": {
                    "news": str(news_dir / "20260310.jsonl"),
                    "classifications": str(cls_dir / "20260310.jsonl"),
                    "daily_prices": str(tmp_path / "data" / "collector" / "daily_prices" / "20260310.jsonl"),
                    "daily_index": str(tmp_path / "data" / "collector" / "index" / "20260310.jsonl"),
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(collector_manifests_dir=manifests_dir)

    payload = load_collector_day_bundle(cfg, "20260310")

    assert payload["manifest"]["status"] == "complete"
    assert payload["artifacts"]["news"]["records"][0]["news_id"] == "n1"
    assert payload["artifacts"]["classifications"]["records"][0]["bucket"] == "POS_STRONG"


def test_list_runtime_dates_reads_runtime_index(tmp_path):
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:10:00+09:00",
                        "artifacts": {
                            "price_snapshots": {
                                "path": str(runtime_dir / "price_snapshots" / "20260316.jsonl"),
                                "exists": True,
                                "recorded_at": "2026-03-16T09:10:00+09:00",
                            }
                        },
                    },
                    {
                        "date": "20260315",
                        "generated_at": "2026-03-15T15:35:00+09:00",
                        "artifacts": {
                            "context_cards": {
                                "path": str(runtime_dir / "context_cards" / "20260315.jsonl"),
                                "exists": True,
                                "recorded_at": "2026-03-15T14:05:00+09:00",
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(runtime_index_path=runtime_dir / "index.json")

    assert list_runtime_dates(cfg) == ["20260316", "20260315"]


def test_load_runtime_day_manifest_reads_runtime_index_entry(tmp_path):
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:10:00+09:00",
                        "artifacts": {
                            "market_context": {
                                "path": str(runtime_dir / "market_context" / "20260316.jsonl"),
                                "exists": True,
                                "recorded_at": "2026-03-16T09:10:00+09:00",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(runtime_index_path=runtime_dir / "index.json")

    payload = load_runtime_day_manifest(cfg, "20260316")

    assert payload["date"] == "20260316"
    assert payload["artifacts"]["market_context"]["exists"] is True


def test_load_runtime_day_artifacts_reads_jsonl_records(tmp_path):
    runtime_dir = tmp_path / "data" / "runtime"
    (runtime_dir / "context_cards").mkdir(parents=True)
    (runtime_dir / "price_snapshots").mkdir(parents=True)

    context_path = runtime_dir / "context_cards" / "20260316.jsonl"
    price_path = runtime_dir / "price_snapshots" / "20260316.jsonl"
    context_path.write_text(json.dumps({"event_id": "evt_001", "bucket": "POS_STRONG"}) + "\n", encoding="utf-8")
    price_path.write_text(json.dumps({"event_id": "evt_001", "horizon": "t0", "px": 70000.0}) + "\n", encoding="utf-8")

    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:10:00+09:00",
                        "artifacts": {
                            "context_cards": {
                                "path": str(context_path),
                                "exists": True,
                                "recorded_at": "2026-03-16T09:10:00+09:00",
                            },
                            "price_snapshots": {
                                "path": str(price_path),
                                "exists": True,
                                "recorded_at": "2026-03-16T09:11:00+09:00",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(runtime_index_path=runtime_dir / "index.json")

    payload = load_runtime_day_artifacts(cfg, "20260316")

    assert payload["artifacts"]["context_cards"]["records"][0]["event_id"] == "evt_001"
    assert payload["artifacts"]["price_snapshots"]["records"][0]["px"] == 70000.0


async def test_replay_no_events(tmp_path):
    log_file = _write_events(tmp_path, [{"type": "event", "bucket": "NEG_STRONG"}])
    cfg = Config(log_dir=tmp_path / "replay_logs")
    await replay(log_file, cfg)
    # Should complete without error


async def test_replay_uses_price_snapshots(tmp_path):
    """Replay should prefer price_snapshot t0/close over pykrx fallback."""
    evt = _make_event()
    t0_snap = {"type": "price_snapshot", "event_id": "evt_001", "horizon": "t0", "px": 70000.0}
    close_snap = {"type": "price_snapshot", "event_id": "evt_001", "horizon": "close", "px": 72100.0}
    log_file = _write_events(tmp_path, [evt, t0_snap, close_snap])
    cfg = Config(log_dir=tmp_path / "replay_logs", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 85
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)
        mock_prices.return_value = {}  # Should NOT be used

        await replay(log_file, cfg)
        # pykrx fallback should NOT have been called since snapshots exist
        mock_prices.assert_not_called()


async def test_replay_with_buy_decision(tmp_path):
    events = [_make_event()]
    log_file = _write_events(tmp_path, events)
    cfg = Config(log_dir=tmp_path / "replay_logs", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 80
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)
        mock_prices.return_value = {"open": 70000, "close": 72000, "high": 73000, "low": 69000}

        await replay(log_file, cfg)


async def test_replay_passes_normalized_guardrail_context(tmp_path):
    evt = _make_event()
    evt["ctx"]["intraday_value_vs_adv20d"] = 0.005
    evt["ctx"]["top_ask_notional"] = 4_000_000.0
    evt["ctx"]["quote_temp_stop"] = True
    evt["ctx"]["quote_liquidation_trade"] = False
    log_file = _write_events(tmp_path, [evt])
    cfg = Config(log_dir=tmp_path / "replay_logs", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 80
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=False, reason="TEMP_STOP")
        mock_prices.return_value = {}

        await replay(log_file, cfg)

    assert mock_gr.call_args is not None
    assert mock_gr.call_args.kwargs["intraday_value_vs_adv20d"] == 0.005
    assert mock_gr.call_args.kwargs["top_ask_notional"] == 4_000_000.0
    assert mock_gr.call_args.kwargs["quote_temp_stop"] is True
    assert mock_gr.call_args.kwargs["quote_liquidation_trade"] is False
    assert mock_gr.call_args.kwargs["decision_time_kst"] == datetime.fromisoformat(evt["detected_at"])
    assert mock_gr.call_args.kwargs["decision_hold_minutes"] == 20


async def test_replay_runtime_date_uses_runtime_index_artifacts(tmp_path):
    runtime_dir = tmp_path / "data" / "runtime"
    (runtime_dir / "context_cards").mkdir(parents=True)
    (runtime_dir / "price_snapshots").mkdir(parents=True)
    (runtime_dir / "market_context").mkdir(parents=True)

    context_path = runtime_dir / "context_cards" / "20260316.jsonl"
    price_path = runtime_dir / "price_snapshots" / "20260316.jsonl"
    market_path = runtime_dir / "market_context" / "20260316.jsonl"
    context_path.write_text(
        json.dumps(
            {
                "type": "context_card",
                "event_id": "evt_001",
                "ticker": "005930",
                "corp_name": "삼성전자",
                "headline": "반도체 공급계약 체결",
                "bucket": "POS_STRONG",
                "quant_check_passed": True,
                "detected_at": "2026-03-16T09:12:04+09:00",
                "disclosed_at": "2026-03-16T09:12:00+09:00",
                "ctx": {"ret_today": 3.5, "adv_value_20d": 10e9, "spread_bps": 8.0},
            }
        ) + "\n",
        encoding="utf-8",
    )
    price_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "price_snapshot", "event_id": "evt_001", "horizon": "t0", "px": 70000.0}),
                json.dumps({"type": "price_snapshot", "event_id": "evt_001", "horizon": "close", "px": 72100.0}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    market_path.write_text(json.dumps({"type": "market_context", "ts": "2026-03-16T09:10:00+09:00"}) + "\n", encoding="utf-8")
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:35:00+09:00",
                        "artifacts": {
                            "context_cards": {"path": str(context_path), "exists": True, "recorded_at": "2026-03-16T09:12:04+09:00"},
                            "price_snapshots": {"path": str(price_path), "exists": True, "recorded_at": "2026-03-16T15:35:00+09:00"},
                            "market_context": {"path": str(market_path), "exists": True, "recorded_at": "2026-03-16T09:10:00+09:00"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(log_dir=tmp_path / "replay_logs", runtime_index_path=runtime_dir / "index.json", anthropic_api_key="test")

    mock_decision = MagicMock()
    mock_decision.action.value = "BUY"
    mock_decision.confidence = 85
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)
        mock_prices.return_value = {}

        await replay_runtime_date("20260316", cfg)

    mock_prices.assert_not_called()


async def test_replay_day_merges_runtime_and_collector_sources(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    collector_news_dir = tmp_path / "data" / "collector" / "news"
    collector_cls_dir = tmp_path / "data" / "collector" / "classifications"
    runtime_dir = tmp_path / "data" / "runtime"
    collector_manifests_dir.mkdir(parents=True)
    collector_news_dir.mkdir(parents=True)
    collector_cls_dir.mkdir(parents=True)
    (runtime_dir / "context_cards").mkdir(parents=True)
    (runtime_dir / "price_snapshots").mkdir(parents=True)

    # Runtime event should win over matching collector headline+ticker.
    (runtime_dir / "context_cards" / "20260316.jsonl").write_text(
        json.dumps(
            {
                "type": "context_card",
                "event_id": "evt_runtime",
                "ticker": "005930",
                "corp_name": "삼성전자",
                "headline": "반도체 공급계약 체결",
                "bucket": "POS_STRONG",
                "quant_check_passed": True,
                "detected_at": "2026-03-16T09:12:04+09:00",
                "disclosed_at": "2026-03-16T09:12:00+09:00",
                "ctx": {"ret_today": 3.5, "adv_value_20d": 10e9, "spread_bps": 8.0},
            }
        ) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "price_snapshots" / "20260316.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "price_snapshot", "event_id": "evt_runtime", "horizon": "t0", "px": 70000.0}),
                json.dumps({"type": "price_snapshot", "event_id": "evt_runtime", "horizon": "close", "px": 72100.0}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:35:00+09:00",
                        "artifacts": {
                            "context_cards": {"path": str(runtime_dir / "context_cards" / "20260316.jsonl"), "exists": True, "recorded_at": "2026-03-16T09:12:04+09:00"},
                            "price_snapshots": {"path": str(runtime_dir / "price_snapshots" / "20260316.jsonl"), "exists": True, "recorded_at": "2026-03-16T15:35:00+09:00"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    (collector_news_dir / "20260316.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"news_id": "n1", "date": "20260316", "time": "091200", "title": "반도체 공급계약 체결", "tickers": ["005930"], "dorg": "삼성전자", "collected_at": "2026-03-16T09:13:00+09:00"}),
                json.dumps({"news_id": "n2", "date": "20260316", "time": "101500", "title": "신규사업 진출", "tickers": ["000660"], "dorg": "SK하이닉스", "collected_at": "2026-03-16T10:16:00+09:00"}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    (collector_cls_dir / "20260316.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"news_id": "n1", "bucket": "POS_STRONG", "title": "반도체 공급계약 체결", "tickers": ["005930"]}),
                json.dumps({"news_id": "n2", "bucket": "POS_STRONG", "title": "신규사업 진출", "tickers": ["000660"]}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    (collector_manifests_dir / "20260316.json").write_text(
        json.dumps(
            {
                "date": "20260316",
                "status": "complete",
                "counts": {"news": 2, "classifications": 2, "daily_prices": 0, "daily_index": 0},
                "paths": {
                    "news": str(collector_news_dir / "20260316.jsonl"),
                    "classifications": str(collector_cls_dir / "20260316.jsonl"),
                    "daily_prices": str(tmp_path / "data" / "collector" / "daily_prices" / "20260316.jsonl"),
                    "daily_index": str(tmp_path / "data" / "collector" / "index" / "20260316.jsonl"),
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = Config(
        log_dir=tmp_path / "replay_logs",
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_reports_dir=tmp_path / "data" / "replay" / "day_reports",
        anthropic_api_key="test",
    )

    decided_tickers: list[str] = []

    def _decide_side_effect(**kwargs):
        decided_tickers.append(kwargs["ticker"])
        decision = MagicMock()
        decision.action.value = "BUY"
        decision.confidence = 80
        decision.mode = "replay"
        decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')
        return decision

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr, \
         patch("kindshot.replay._fetch_post_hoc_prices", new_callable=AsyncMock) as mock_prices:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(side_effect=_decide_side_effect)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)
        mock_prices.return_value = {"open": 100000, "close": 102000, "high": 103000, "low": 99000}

        report = await replay_day("20260316", cfg)

    assert decided_tickers.count("005930") == 1
    assert decided_tickers.count("000660") == 1
    mock_prices.assert_called_once()
    assert report["input"]["merge"]["collector_deduped"] == 1
    assert report["input"]["merge"]["collector_added"] == 1
    assert report["summary"]["total_actionable_events"] == 2
    report_path = tmp_path / "data" / "replay" / "day_reports" / "20260316.json"
    assert report_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["date"] == "20260316"
    assert persisted["input"]["collector"]["available"] is True
    assert persisted["input"]["runtime"]["available"] is True


async def test_replay_day_supports_explicit_report_output_path(tmp_path):
    runtime_dir = tmp_path / "data" / "runtime"
    (runtime_dir / "context_cards").mkdir(parents=True)
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:35:00+09:00",
                        "artifacts": {
                            "context_cards": {
                                "path": str(runtime_dir / "context_cards" / "20260316.jsonl"),
                                "exists": True,
                                "recorded_at": "2026-03-16T09:12:04+09:00",
                            }
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "context_cards" / "20260316.jsonl").write_text(
        json.dumps(
            {
                "type": "context_card",
                "event_id": "evt_runtime",
                "ticker": "005930",
                "corp_name": "삼성전자",
                "headline": "반도체 공급계약 체결",
                "bucket": "POS_STRONG",
                "quant_check_passed": True,
                "detected_at": "2026-03-16T09:12:04+09:00",
                "disclosed_at": "2026-03-16T09:12:00+09:00",
                "ctx": {"ret_today": 3.5, "adv_value_20d": 10e9, "spread_bps": 8.0},
            }
        ) + "\n",
        encoding="utf-8",
    )
    cfg = Config(log_dir=tmp_path / "replay_logs", runtime_index_path=runtime_dir / "index.json", anthropic_api_key="test")
    out_path = tmp_path / "custom" / "report.json"

    mock_decision = MagicMock()
    mock_decision.action.value = "SKIP"
    mock_decision.confidence = 55
    mock_decision.mode = "replay"
    mock_decision.model_dump_json = MagicMock(return_value='{"type":"decision"}')

    with patch("kindshot.replay.DecisionEngine") as MockEngine, \
         patch("kindshot.replay.check_guardrails") as mock_gr:
        engine_instance = MockEngine.return_value
        engine_instance.decide = AsyncMock(return_value=mock_decision)
        from kindshot.guardrails import GuardrailResult
        mock_gr.return_value = GuardrailResult(passed=True)

        report = await replay_day("20260316", cfg, report_output_path=str(out_path))

    assert out_path.exists()
    persisted = json.loads(out_path.read_text(encoding="utf-8"))
    assert persisted["summary"]["skip_decisions"] == 1
    assert report["summary"]["skip_decisions"] == 1


def test_replay_day_status_reports_ready_inputs(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    collector_news_dir = tmp_path / "data" / "collector" / "news"
    collector_cls_dir = tmp_path / "data" / "collector" / "classifications"
    runtime_dir = tmp_path / "data" / "runtime"
    collector_manifests_dir.mkdir(parents=True)
    collector_news_dir.mkdir(parents=True)
    collector_cls_dir.mkdir(parents=True)
    (runtime_dir / "context_cards").mkdir(parents=True)
    (runtime_dir / "price_snapshots").mkdir(parents=True)
    (runtime_dir / "market_context").mkdir(parents=True)

    (collector_news_dir / "20260316.jsonl").write_text(
        json.dumps({"news_id": "n1", "date": "20260316", "time": "091200", "title": "반도체 공급계약 체결", "tickers": ["005930"], "dorg": "삼성전자", "collected_at": "2026-03-16T09:13:00+09:00"}) + "\n",
        encoding="utf-8",
    )
    (collector_cls_dir / "20260316.jsonl").write_text(
        json.dumps({"news_id": "n1", "bucket": "POS_STRONG", "title": "반도체 공급계약 체결", "tickers": ["005930"]}) + "\n",
        encoding="utf-8",
    )
    (collector_manifests_dir / "20260316.json").write_text(
        json.dumps(
            {
                "date": "20260316",
                "status": "complete",
                "counts": {"news": 1, "classifications": 1, "daily_prices": 0, "daily_index": 0},
                "paths": {
                    "news": str(collector_news_dir / "20260316.jsonl"),
                    "classifications": str(collector_cls_dir / "20260316.jsonl"),
                    "daily_prices": str(tmp_path / "data" / "collector" / "daily_prices" / "20260316.jsonl"),
                    "daily_index": str(tmp_path / "data" / "collector" / "index" / "20260316.jsonl"),
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "context_cards" / "20260316.jsonl").write_text(
        json.dumps({"type": "context_card", "event_id": "evt_runtime", "ticker": "005930", "headline": "반도체 공급계약 체결", "bucket": "POS_STRONG", "quant_check_passed": True}) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "price_snapshots" / "20260316.jsonl").write_text(
        json.dumps({"type": "price_snapshot", "event_id": "evt_runtime", "horizon": "t0", "px": 70000.0}) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "market_context" / "20260316.jsonl").write_text(
        json.dumps({"type": "market_context", "ts": "2026-03-16T09:10:00+09:00"}) + "\n",
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T10:00:00+09:00",
                "entries": [
                    {
                        "date": "20260316",
                        "generated_at": "2026-03-16T09:35:00+09:00",
                        "artifacts": {
                            "context_cards": {"path": str(runtime_dir / "context_cards" / "20260316.jsonl"), "exists": True, "recorded_at": "2026-03-16T09:12:04+09:00"},
                            "price_snapshots": {"path": str(runtime_dir / "price_snapshots" / "20260316.jsonl"), "exists": True, "recorded_at": "2026-03-16T15:35:00+09:00"},
                            "market_context": {"path": str(runtime_dir / "market_context" / "20260316.jsonl"), "exists": True, "recorded_at": "2026-03-16T09:10:00+09:00"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=tmp_path / "data" / "replay" / "day_status",
    )

    report = replay_day_status("20260316", cfg)

    assert report["health"] == "ready"
    assert report["warnings"] == []
    status_path = tmp_path / "data" / "replay" / "day_status" / "20260316.json"
    assert status_path.exists()


def test_replay_day_status_reports_partial_inputs(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    collector_manifests_dir.mkdir(parents=True)
    manifest_path = collector_manifests_dir / "20260316.json"
    manifest_path.write_text(
        json.dumps(
            {
                "date": "20260316",
                "status": "partial",
                "status_reason": "daily_index_missing",
                "generated_at": "2026-03-16T00:03:00+09:00",
                "counts": {"news": 0, "classifications": 0, "daily_prices": 0, "daily_index": 0},
                "paths": {
                    "news": str(tmp_path / "missing-news.jsonl"),
                    "classifications": str(tmp_path / "missing-classifications.jsonl"),
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
        replay_day_status_dir=tmp_path / "data" / "replay" / "day_status",
    )

    report = replay_day_status("20260316", cfg)

    assert report["health"] == "partial_inputs"
    assert "COLLECTOR_PARTIAL_STATUS" in report["warnings"]
    assert "RUNTIME_CONTEXT_CARDS_MISSING" in report["warnings"]
    assert "NO_REPLAYABLE_EVENTS" in report["warnings"]
    assert report["input"]["collector"]["status_reason"] == "daily_index_missing"
    assert report["input"]["collector"]["manifest_path"] == str(manifest_path)
    assert report["input"]["collector"]["generated_at"] == "2026-03-16T00:03:00+09:00"


def test_replay_day_status_reports_missing_inputs_with_override(tmp_path):
    cfg = Config(
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
    )
    out_path = tmp_path / "custom" / "status.json"

    report = replay_day_status("20260316", cfg, output_path=str(out_path))

    assert report["health"] == "missing_inputs"
    assert "COLLECTOR_MANIFEST_MISSING" in report["warnings"]
    assert out_path.exists()


def test_replay_ops_summary_aggregates_multiple_dates(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    runtime_dir = tmp_path / "data" / "runtime"
    day_status_dir = tmp_path / "data" / "replay" / "day_status"
    day_reports_dir = tmp_path / "data" / "replay" / "day_reports"
    collector_manifests_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    day_status_dir.mkdir(parents=True)
    day_reports_dir.mkdir(parents=True)

    (collector_manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-16T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260316.json")},
                    {"date": "20260315", "status": "partial", "has_partial_data": True, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260315.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "generated_at": "2026-03-16T00:02:00+09:00", "artifacts": {}},
                    {"date": "20260314", "generated_at": "2026-03-14T00:02:00+09:00", "artifacts": {}},
                ],
            }
        ),
        encoding="utf-8",
    )
    (day_status_dir / "20260316.json").write_text(
        json.dumps(
            {
                "date": "20260316",
                "generated_at": "2026-03-16T10:00:00+09:00",
                "health": "ready",
                "warnings": [],
                "input": {"collector": {"available": True}, "runtime": {"available": True}},
                "replayability": {"merged_event_count": 3},
            }
        ),
        encoding="utf-8",
    )
    (day_status_dir / "20260315.json").write_text(
        json.dumps(
            {
                "date": "20260315",
                "generated_at": "2026-03-15T10:00:00+09:00",
                "health": "partial_inputs",
                "warnings": ["COLLECTOR_PARTIAL_STATUS", "NO_REPLAYABLE_EVENTS"],
                "input": {
                    "collector": {
                        "available": True,
                        "status_reason": "daily_index_missing",
                        "manifest_path": str(collector_manifests_dir / "20260315.json"),
                    },
                    "runtime": {"available": False},
                },
                "replayability": {"merged_event_count": 0},
            }
        ),
        encoding="utf-8",
    )
    (day_status_dir / "20260314.json").write_text(
        json.dumps(
            {
                "date": "20260314",
                "generated_at": "2026-03-14T10:00:00+09:00",
                "health": "runtime_only",
                "warnings": ["COLLECTOR_MANIFEST_MISSING"],
                "input": {"collector": {"available": False}, "runtime": {"available": True}},
                "replayability": {"merged_event_count": 1},
            }
        ),
        encoding="utf-8",
    )
    (day_reports_dir / "20260316.json").write_text(
        json.dumps({"summary": {"buy_decisions": 2, "price_data_trades": 2}}),
        encoding="utf-8",
    )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=day_status_dir,
        replay_day_reports_dir=day_reports_dir,
        replay_ops_summary_path=tmp_path / "data" / "replay" / "ops" / "latest.json",
    )

    report = replay_ops_summary(cfg, limit=2)

    assert report["date_count"] == 3
    assert report["health_counts"]["ready"] == 1
    assert report["health_counts"]["partial_inputs"] == 1
    assert report["health_counts"]["runtime_only"] == 1
    assert report["warning_counts"]["COLLECTOR_PARTIAL_STATUS"] == 1
    assert len(report["rows"]) == 2
    assert report["rows"][0]["date"] == "20260316"
    assert report["rows"][0]["buy_decisions"] == 2
    assert report["all_rows"][1]["collector_status_reason"] == "daily_index_missing"
    assert report["all_rows"][1]["collector_manifest_path"].endswith("20260315.json")
    assert (tmp_path / "data" / "replay" / "ops" / "latest.json").exists()


def test_replay_ops_summary_supports_explicit_output_path(tmp_path):
    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    cfg = Config(
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=tmp_path / "data" / "replay" / "day_status",
    )
    out_path = tmp_path / "custom" / "ops.json"

    report = replay_ops_summary(cfg, limit=5, output_path=str(out_path))

    assert report["date_count"] == 0
    assert out_path.exists()


def test_replay_ops_queue_ready_applies_policy_filters(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    runtime_dir = tmp_path / "data" / "runtime"
    day_status_dir = tmp_path / "data" / "replay" / "day_status"
    day_reports_dir = tmp_path / "data" / "replay" / "day_reports"
    collector_manifests_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    day_status_dir.mkdir(parents=True)
    day_reports_dir.mkdir(parents=True)

    (collector_manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-16T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260316.json")},
                    {"date": "20260315", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260315.json")},
                    {"date": "20260314", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-14T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260314.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    (day_status_dir / "20260316.json").write_text(
        json.dumps({"date": "20260316", "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": False}}, "replayability": {"merged_event_count": 3}}),
        encoding="utf-8",
    )
    (day_status_dir / "20260315.json").write_text(
        json.dumps({"date": "20260315", "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 1}}),
        encoding="utf-8",
    )
    (day_status_dir / "20260314.json").write_text(
        json.dumps(
            {
                "date": "20260314",
                "health": "partial_inputs",
                "warnings": ["COLLECTOR_PARTIAL_STATUS"],
                "input": {
                    "collector": {
                        "available": True,
                        "status_reason": "pagination_truncated",
                        "manifest_path": str(collector_manifests_dir / "20260314.json"),
                    },
                    "runtime": {"available": True},
                },
                "replayability": {"merged_event_count": 5},
            }
        ),
        encoding="utf-8",
    )
    (day_reports_dir / "20260315.json").write_text(
        json.dumps({"summary": {"buy_decisions": 1, "price_data_trades": 1}}),
        encoding="utf-8",
    )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=day_status_dir,
        replay_day_reports_dir=day_reports_dir,
        replay_ops_queue_ready_path=tmp_path / "data" / "replay" / "ops" / "queue_ready_latest.json",
    )

    report = replay_ops_queue_ready(
        cfg,
        limit=2,
        require_runtime=True,
        min_merged_events=2,
    )

    assert report["policy"]["require_runtime"] is True
    assert report["policy"]["min_merged_events"] == 2
    assert report["candidate_count"] == 2
    assert report["selected_count"] == 0
    assert report["skipped_counts"]["missing_runtime"] == 1
    assert report["skipped_counts"]["existing_report"] == 1
    assert report["skipped_counts"]["health_not_ready"] == 1
    rows = {row["date"]: row for row in report["rows"]}
    assert rows["20260316"]["selection_reason"] == "missing_runtime"
    assert rows["20260315"]["selection_reason"] == "existing_report"
    assert rows["20260314"]["selection_reason"] == "health_not_ready"
    assert rows["20260314"]["collector_status_reason"] == "pagination_truncated"
    assert rows["20260314"]["collector_manifest_path"].endswith("20260314.json")
    assert (tmp_path / "data" / "replay" / "ops" / "queue_ready_latest.json").exists()


async def test_replay_ops_run_ready_executes_ready_dates_without_reports(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    runtime_dir = tmp_path / "data" / "runtime"
    day_status_dir = tmp_path / "data" / "replay" / "day_status"
    day_reports_dir = tmp_path / "data" / "replay" / "day_reports"
    collector_manifests_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    day_status_dir.mkdir(parents=True)
    day_reports_dir.mkdir(parents=True)

    (collector_manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-16T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260316.json")},
                    {"date": "20260315", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260315.json")},
                    {"date": "20260314", "status": "partial", "has_partial_data": True, "generated_at": "2026-03-14T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260314.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    (day_status_dir / "20260316.json").write_text(
        json.dumps({"date": "20260316", "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 3}}),
        encoding="utf-8",
    )
    (day_status_dir / "20260315.json").write_text(
        json.dumps({"date": "20260315", "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 2}}),
        encoding="utf-8",
    )
    (day_status_dir / "20260314.json").write_text(
        json.dumps({"date": "20260314", "health": "partial_inputs", "warnings": ["COLLECTOR_PARTIAL_STATUS"], "input": {"collector": {"available": True}, "runtime": {"available": False}}, "replayability": {"merged_event_count": 0}}),
        encoding="utf-8",
    )
    # Existing day report should prevent re-run.
    (day_reports_dir / "20260315.json").write_text(
        json.dumps({"summary": {"buy_decisions": 1, "price_data_trades": 1}}),
        encoding="utf-8",
    )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=day_status_dir,
        replay_day_reports_dir=day_reports_dir,
        replay_ops_run_ready_path=tmp_path / "data" / "replay" / "ops" / "run_ready_latest.json",
    )

    async def _fake_replay_day(dt: str, config: Config, report_output_path: str = ""):
        path = config.replay_day_reports_dir / f"{dt}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": dt,
            "summary": {"buy_decisions": 2, "price_data_trades": 2},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    with patch("kindshot.replay.replay_day", new=AsyncMock(side_effect=_fake_replay_day)) as mock_replay_day:
        report = await replay_ops_run_ready(cfg, limit=5)

    mock_replay_day.assert_awaited_once()
    assert mock_replay_day.await_args.args[0] == "20260316"
    assert report["candidate_count"] == 2
    assert report["selected_count"] == 1
    assert report["executed_count"] == 1
    assert report["skipped_counts"]["existing_report"] == 1
    rows = {row["date"]: row for row in report["rows"]}
    assert rows["20260316"]["executed"] is True
    assert rows["20260316"]["selection_reason"] == "selected"
    assert rows["20260315"]["executed"] is False
    assert rows["20260315"]["selection_reason"] == "existing_report"
    assert (tmp_path / "data" / "replay" / "ops" / "run_ready_latest.json").exists()


async def test_replay_ops_run_ready_supports_explicit_output_path(tmp_path):
    cfg = Config(
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        runtime_index_path=tmp_path / "data" / "runtime" / "index.json",
        replay_day_status_dir=tmp_path / "data" / "replay" / "day_status",
        replay_day_reports_dir=tmp_path / "data" / "replay" / "day_reports",
    )
    out_path = tmp_path / "custom" / "run_ready.json"

    (tmp_path / "data" / "collector" / "manifests").mkdir(parents=True)
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    ((tmp_path / "data" / "collector" / "manifests") / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    ((tmp_path / "data" / "runtime") / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )

    report = await replay_ops_run_ready(cfg, limit=3, output_path=str(out_path))

    assert report["candidate_count"] == 0
    assert out_path.exists()


async def test_replay_ops_run_ready_can_rerun_reported_dates_with_filters(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    runtime_dir = tmp_path / "data" / "runtime"
    day_status_dir = tmp_path / "data" / "replay" / "day_status"
    day_reports_dir = tmp_path / "data" / "replay" / "day_reports"
    collector_manifests_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    day_status_dir.mkdir(parents=True)
    day_reports_dir.mkdir(parents=True)

    (collector_manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-16T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260316.json")},
                    {"date": "20260315", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260315.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    (day_status_dir / "20260316.json").write_text(
        json.dumps({"date": "20260316", "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 4}}),
        encoding="utf-8",
    )
    (day_status_dir / "20260315.json").write_text(
        json.dumps({"date": "20260315", "health": "ready", "warnings": [], "input": {"collector": {"available": False}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 4}}),
        encoding="utf-8",
    )
    (day_reports_dir / "20260316.json").write_text(
        json.dumps({"summary": {"buy_decisions": 1, "price_data_trades": 1}}),
        encoding="utf-8",
    )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=day_status_dir,
        replay_day_reports_dir=day_reports_dir,
    )

    async def _fake_replay_day(dt: str, config: Config, report_output_path: str = ""):
        return {"date": dt, "summary": {"buy_decisions": 3, "price_data_trades": 2}}

    with patch("kindshot.replay.replay_day", new=AsyncMock(side_effect=_fake_replay_day)) as mock_replay_day:
        report = await replay_ops_run_ready(
            cfg,
            limit=2,
            include_reported=True,
            require_runtime=True,
            require_collector=True,
            min_merged_events=2,
        )

    mock_replay_day.assert_awaited_once()
    assert mock_replay_day.await_args.args[0] == "20260316"
    assert report["selected_count"] == 1
    assert report["executed_count"] == 1
    rows = {row["date"]: row for row in report["rows"]}
    assert rows["20260316"]["selection_reason"] == "selected"
    assert rows["20260315"]["selection_reason"] == "missing_collector"


async def test_replay_ops_cycle_ready_stops_on_first_error_by_default(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    runtime_dir = tmp_path / "data" / "runtime"
    day_status_dir = tmp_path / "data" / "replay" / "day_status"
    collector_manifests_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    day_status_dir.mkdir(parents=True)

    (collector_manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-16T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260316.json")},
                    {"date": "20260315", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260315.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    for dt in ("20260316", "20260315"):
        (day_status_dir / f"{dt}.json").write_text(
            json.dumps({"date": dt, "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 3}}),
            encoding="utf-8",
        )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=day_status_dir,
        replay_day_reports_dir=tmp_path / "data" / "replay" / "day_reports",
        replay_ops_summary_path=tmp_path / "data" / "replay" / "ops" / "latest.json",
        replay_ops_queue_ready_path=tmp_path / "data" / "replay" / "ops" / "queue_ready_latest.json",
        replay_ops_run_ready_path=tmp_path / "data" / "replay" / "ops" / "run_ready_latest.json",
        replay_ops_cycle_ready_path=tmp_path / "data" / "replay" / "ops" / "cycle_ready_latest.json",
    )

    async def _fake_replay_day(dt: str, config: Config, report_output_path: str = ""):
        if dt == "20260316":
            raise RuntimeError("boom")
        return {"date": dt, "summary": {"buy_decisions": 1, "price_data_trades": 1}}

    with patch("kindshot.replay.replay_day", new=AsyncMock(side_effect=_fake_replay_day)) as mock_replay_day:
        report = await replay_ops_cycle_ready(cfg, limit=2)

    assert mock_replay_day.await_count == 1
    assert report["failed_count"] == 1
    assert report["executed_count"] == 0
    assert report["stopped_early"] is True
    rows = {row["date"]: row for row in report["rows"]}
    assert rows["20260316"]["error"] == "RuntimeError: boom"
    assert (tmp_path / "data" / "replay" / "ops" / "cycle_ready_latest.json").exists()


async def test_replay_ops_cycle_ready_can_continue_on_error(tmp_path):
    collector_manifests_dir = tmp_path / "data" / "collector" / "manifests"
    runtime_dir = tmp_path / "data" / "runtime"
    day_status_dir = tmp_path / "data" / "replay" / "day_status"
    collector_manifests_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    day_status_dir.mkdir(parents=True)

    (collector_manifests_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-03-16T00:00:00+09:00",
                "entries": [
                    {"date": "20260316", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-16T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260316.json")},
                    {"date": "20260315", "status": "complete", "has_partial_data": False, "generated_at": "2026-03-15T00:01:00+09:00", "manifest_path": str(collector_manifests_dir / "20260315.json")},
                ],
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "index.json").write_text(
        json.dumps({"generated_at": "2026-03-16T00:00:00+09:00", "entries": []}),
        encoding="utf-8",
    )
    for dt in ("20260316", "20260315"):
        (day_status_dir / f"{dt}.json").write_text(
            json.dumps({"date": dt, "health": "ready", "warnings": [], "input": {"collector": {"available": True}, "runtime": {"available": True}}, "replayability": {"merged_event_count": 3}}),
            encoding="utf-8",
        )

    cfg = Config(
        collector_manifests_dir=collector_manifests_dir,
        runtime_index_path=runtime_dir / "index.json",
        replay_day_status_dir=day_status_dir,
        replay_day_reports_dir=tmp_path / "data" / "replay" / "day_reports",
        replay_ops_summary_path=tmp_path / "data" / "replay" / "ops" / "latest.json",
        replay_ops_queue_ready_path=tmp_path / "data" / "replay" / "ops" / "queue_ready_latest.json",
        replay_ops_run_ready_path=tmp_path / "data" / "replay" / "ops" / "run_ready_latest.json",
        replay_ops_cycle_ready_path=tmp_path / "data" / "replay" / "ops" / "cycle_ready_latest.json",
    )

    async def _fake_replay_day(dt: str, config: Config, report_output_path: str = ""):
        if dt == "20260316":
            raise RuntimeError("boom")
        path = config.replay_day_reports_dir / f"{dt}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"summary": {"buy_decisions": 2, "price_data_trades": 1}}), encoding="utf-8")
        return {"date": dt, "summary": {"buy_decisions": 2, "price_data_trades": 1}}

    with patch("kindshot.replay.replay_day", new=AsyncMock(side_effect=_fake_replay_day)) as mock_replay_day:
        report = await replay_ops_cycle_ready(cfg, limit=2, continue_on_error=True)

    assert mock_replay_day.await_count == 2
    assert report["failed_count"] == 1
    assert report["executed_count"] == 1
    assert report["stopped_early"] is False
    rows = {row["date"]: row for row in report["rows"]}
    assert rows["20260316"]["error"] == "RuntimeError: boom"
    assert rows["20260315"]["executed"] is True
    assert (tmp_path / "data" / "replay" / "ops" / "latest.json").exists()
