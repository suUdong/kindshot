"""Tests for historical collector backfill flow."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kindshot.collector import (
    BackfillResult,
    CollectionDayManifest,
    CollectionManifestIndexEntry,
    CollectionLogRecord,
    CollectionLogSummary,
    CollectorState,
    _build_status_report,
    _build_status_detail,
    _backfill_report_output_path,
    _compute_status_health,
    _collect_all_news_for_date,
    _collect_daily_index,
    _is_trusted_complete_date,
    _load_latest_collection_statuses,
    _parse_collect_args,
    _parse_status_args,
    _resolve_backfill_range,
    _collect_news_for_date_with_retry,
    _manifest_index_path,
    _manifest_path,
    append_news_items,
    append_collection_log,
    build_collection_backfill_report,
    collect_main,
    compute_finalized_date,
    log_collection_status,
    load_collection_log_summary,
    load_collection_status_report,
    load_collector_state,
    print_collection_backfill_json,
    print_collection_status_json,
    run_backfill,
    save_collector_state,
    update_collection_manifest_index,
    write_collection_backfill_report,
    write_collection_day_manifest,
)
from kindshot.config import Config
from kindshot.kis_client import NewsDisclosure, NewsDisclosureFetchResult


def test_compute_finalized_date_before_cutoff_uses_today_minus_two():
    now = datetime(2026, 3, 14, 1, 0, tzinfo=timezone(timedelta(hours=9)))
    assert compute_finalized_date(now, cutoff_hour=2, cutoff_minute=30) == "20260312"


def test_compute_finalized_date_after_cutoff_uses_today_minus_one():
    now = datetime(2026, 3, 14, 3, 0, tzinfo=timezone(timedelta(hours=9)))
    assert compute_finalized_date(now, cutoff_hour=2, cutoff_minute=30) == "20260313"


def test_save_and_load_collector_state_round_trip(tmp_path):
    path = tmp_path / "collector_state.json"
    state = CollectorState(cursor_date="20260310", last_completed_date="20260311", finalized_date="20260313")
    save_collector_state(path, state)

    loaded = load_collector_state(path)
    assert loaded.cursor_date == "20260310"
    assert loaded.last_completed_date == "20260311"
    assert loaded.finalized_date == "20260313"
    assert loaded.updated_at


def test_decrement_hhmmss_steps_back_one_second():
    from kindshot.collector import _decrement_hhmmss

    assert _decrement_hhmmss("120000") == "115959"
    assert _decrement_hhmmss("000000") == ""


def test_resolve_backfill_range_from_only_uses_finalized_as_start():
    start, end = _resolve_backfill_range(
        finalized_date="20260313",
        state_cursor_date="",
        from_date="20260301",
    )
    assert start == "20260313"
    assert end == "20260301"


def test_resolve_backfill_range_to_only_uses_state_cursor_or_finalized():
    start, end = _resolve_backfill_range(
        finalized_date="20260313",
        state_cursor_date="20260310",
        to_date="20260305",
    )
    assert start == "20260310"
    assert end == "20260305"


def test_resolve_backfill_range_rejects_cursor_mixed_with_from_to():
    with pytest.raises(ValueError, match="--cursor cannot be combined"):
        _resolve_backfill_range(
            finalized_date="20260313",
            state_cursor_date="",
            cursor="20260310",
            from_date="20260301",
        )


def test_parse_status_args_defaults_and_rejects_invalid():
    assert _parse_status_args([]) == (10, False, "")
    assert _parse_status_args(["--limit", "5"]) == (5, False, "")
    assert _parse_status_args(["--limit", "0"]) == (0, False, "")
    assert _parse_status_args(["--json"]) == (10, True, "")
    assert _parse_status_args(["--limit", "3", "--json"]) == (3, True, "")
    assert _parse_status_args(["--json", "--output", "status.json"]) == (10, True, "status.json")
    with pytest.raises(SystemExit, match="--limit must be an integer"):
        _parse_status_args(["--limit", "bad"])
    with pytest.raises(SystemExit, match="--output requires --json"):
        _parse_status_args(["--output", "status.json"])
    with pytest.raises(SystemExit, match="Usage: kindshot collect status"):
        _parse_status_args(["--bad"])


def test_parse_collect_args_defaults_and_rejects_invalid():
    assert _parse_collect_args([]) == ("", "", "", False, "")
    assert _parse_collect_args(["--cursor", "20260310"]) == ("20260310", "", "", False, "")
    assert _parse_collect_args(["--from", "20260301", "--to", "20260313"]) == ("", "20260301", "20260313", False, "")
    assert _parse_collect_args(["--cursor", "20260310", "--json"]) == ("20260310", "", "", True, "")
    assert _parse_collect_args(["--json", "--output", "backfill.json"]) == ("", "", "", True, "backfill.json")
    with pytest.raises(SystemExit, match="--output requires --json"):
        _parse_collect_args(["--output", "backfill.json"])
    with pytest.raises(SystemExit, match="Usage: kindshot collect backfill"):
        _parse_collect_args(["--bad"])


def test_backfill_report_output_path_prefers_explicit_path(tmp_path):
    cfg = Config(collector_backfill_report_path=tmp_path / "data" / "collector" / "backfill" / "latest.json")

    assert _backfill_report_output_path(cfg) == cfg.collector_backfill_report_path
    assert _backfill_report_output_path(cfg, "custom.json") == Path("custom.json")


def test_append_collection_log_writes_jsonl(tmp_path):
    path = tmp_path / "collector" / "collection_log.jsonl"
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260310",
            status="complete",
            news_count=2,
            classification_count=2,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
        ),
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["date"] == "20260310"
    assert rows[0]["daily_price_count"] == 1
    assert rows[0]["classification_count"] == 2
    assert rows[0]["skip_reason"] == ""


def test_write_collection_day_manifest_writes_json(tmp_path):
    manifest = write_collection_day_manifest(
        tmp_path / "manifests",
        dt="20260310",
        status="complete",
        status_reason="",
        finalized_date="20260310",
        items=[
            NewsDisclosure(news_id="NEWS002", data_dt="20260310", data_tm="101501", title="B", dorg="KIS", tickers=("000660",)),
            NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="101500", title="A", dorg="KIS", tickers=("005930",)),
        ],
        tickers=["000660", "005930"],
        news_count=2,
        classification_count=2,
        price_count=1,
        index_count=2,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "daily_prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
        daily_index_source="kis",
    )

    assert isinstance(manifest, CollectionDayManifest)
    path = _manifest_path(tmp_path / "manifests", "20260310")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["date"] == "20260310"
    assert payload["status"] == "complete"
    assert payload["status_reason"] == ""
    assert payload["has_partial_data"] is False
    assert payload["counts"]["news"] == 2
    assert payload["tickers"] == ["000660", "005930"]
    assert payload["news_range"]["first_news_id"] == "NEWS001"
    assert payload["news_range"]["last_news_id"] == "NEWS002"
    assert payload["news_range"]["start_time"] == "101500"
    assert payload["news_range"]["end_time"] == "101501"
    assert payload["sources"]["daily_prices"] == "pykrx"
    assert payload["sources"]["daily_index"] == "kis"
    assert payload["exists"]["news"] is False
    index_payload = json.loads(_manifest_index_path(tmp_path / "manifests").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["date"] == "20260310"
    assert index_payload["entries"][0]["status"] == "complete"
    assert index_payload["entries"][0]["has_partial_data"] is False


def test_update_collection_manifest_index_upserts_and_orders_dates(tmp_path):
    base_dir = tmp_path / "manifests"
    update_collection_manifest_index(
        base_dir,
        CollectionDayManifest(
            date="20260309",
            status="partial",
            status_reason="pagination_truncated",
            has_partial_data=True,
            finalized_date="20260310",
            generated_at="2026-03-15T00:00:00+09:00",
            tickers=[],
            counts={},
            paths={},
            news_range={},
            sources={},
            exists={},
        ),
    )
    update_collection_manifest_index(
        base_dir,
        CollectionDayManifest(
            date="20260310",
            status="complete",
            status_reason="",
            has_partial_data=False,
            finalized_date="20260310",
            generated_at="2026-03-15T00:01:00+09:00",
            tickers=[],
            counts={},
            paths={},
            news_range={},
            sources={},
            exists={},
        ),
    )
    update_collection_manifest_index(
        base_dir,
        CollectionDayManifest(
            date="20260309",
            status="complete",
            status_reason="",
            has_partial_data=False,
            finalized_date="20260310",
            generated_at="2026-03-15T00:02:00+09:00",
            tickers=[],
            counts={},
            paths={},
            news_range={},
            sources={},
            exists={},
        ),
    )

    payload = json.loads(_manifest_index_path(base_dir).read_text(encoding="utf-8"))
    assert payload["entries"][0]["date"] == "20260310"
    assert payload["entries"][1]["date"] == "20260309"
    assert payload["entries"][1]["status"] == "complete"
    assert payload["entries"][1]["has_partial_data"] is False


async def test_is_trusted_complete_date_requires_manifest(tmp_path):
    cfg = Config(collector_manifests_dir=tmp_path / "data" / "collector" / "manifests")

    with patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)):
        assert await _is_trusted_complete_date(cfg, "20260313") is False


async def test_is_trusted_complete_date_rejects_trading_day_zero_index(tmp_path):
    cfg = Config(collector_manifests_dir=tmp_path / "data" / "collector" / "manifests")
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260313",
        status="complete",
        status_reason="",
        finalized_date="20260313",
        items=[NewsDisclosure(news_id="NEWS001", data_dt="20260313", data_tm="101500", title="A", dorg="KIS", tickers=("005930",))],
        tickers=["005930"],
        news_count=1,
        classification_count=1,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260313.jsonl",
        classifications_path=tmp_path / "classifications" / "20260313.jsonl",
        daily_prices_path=tmp_path / "daily_prices" / "20260313.jsonl",
        daily_index_path=tmp_path / "index" / "20260313.jsonl",
    )

    with patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)):
        assert await _is_trusted_complete_date(cfg, "20260313") is False


async def test_is_trusted_complete_date_rejects_non_trading_day_complete_manifest(tmp_path):
    cfg = Config(collector_manifests_dir=tmp_path / "data" / "collector" / "manifests")
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260315",
        status="complete",
        status_reason="",
        finalized_date="20260315",
        items=[],
        tickers=[],
        news_count=0,
        classification_count=0,
        price_count=0,
        index_count=0,
        news_path=tmp_path / "news" / "20260315.jsonl",
        classifications_path=tmp_path / "classifications" / "20260315.jsonl",
        daily_prices_path=tmp_path / "daily_prices" / "20260315.jsonl",
        daily_index_path=tmp_path / "index" / "20260315.jsonl",
    )

    with patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=False)):
        assert await _is_trusted_complete_date(cfg, "20260315") is False


def test_load_latest_collection_statuses_uses_last_status_per_date(tmp_path):
    path = tmp_path / "collector" / "collection_log.jsonl"
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260310",
            status="error",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            error="boom",
        ),
    )
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260310",
            status="complete",
            news_count=2,
            classification_count=2,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:01:00+09:00",
        ),
    )

    statuses = _load_latest_collection_statuses(path)
    assert statuses == {"20260310": "complete"}


def test_load_latest_collection_statuses_treats_skipped_as_complete(tmp_path):
    path = tmp_path / "collector" / "collection_log.jsonl"
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260310",
            status="complete",
            news_count=2,
            classification_count=2,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
        ),
    )
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260310",
            status="skipped",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:01:00+09:00",
        ),
    )

    statuses = _load_latest_collection_statuses(path)
    assert statuses == {"20260310": "complete"}


def test_load_collection_log_summary_reports_latest_backlog(tmp_path):
    path = tmp_path / "collector" / "collection_log.jsonl"
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260311",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated",
        ),
    )
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260310",
            status="error",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:01:00+09:00",
            error="boom",
        ),
    )
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260309",
            status="complete",
            news_count=2,
            classification_count=2,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:02:00+09:00",
        ),
    )
    append_collection_log(
        path,
        CollectionLogRecord(
            date="20260309",
            status="skipped",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:03:00+09:00",
            skip_reason="already_complete",
        ),
    )

    summary = load_collection_log_summary(path)

    assert isinstance(summary, CollectionLogSummary)
    assert summary.latest_statuses == {
        "20260311": "partial",
        "20260310": "error",
        "20260309": "complete",
    }
    assert summary.partial_dates == ["20260311"]
    assert summary.error_dates == ["20260310"]
    assert summary.tracked_dates == ["20260311", "20260310", "20260309"]
    assert summary.oldest_partial_date == "20260311"
    assert summary.oldest_error_date == "20260310"
    assert summary.oldest_blocked_date == "20260310"
    assert summary.blocked_news_count == 3
    assert summary.blocked_classification_count == 3
    assert summary.blocked_price_count == 1
    assert summary.blocked_index_count == 2
    assert summary.status_generated_at
    assert summary.oldest_blocked_age_seconds >= 0
    assert summary.latest_records["20260309"].status == "skipped"
    assert summary.latest_records["20260310"].error == "boom"


def test_append_news_items_dedups_existing_ids(tmp_path):
    news_dir = tmp_path / "news"
    items = [
        NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="101500", title="A", dorg="KIS", tickers=("005930",)),
        NewsDisclosure(news_id="NEWS002", data_dt="20260310", data_tm="101501", title="B", dorg="KIS", tickers=("000660",)),
    ]

    assert append_news_items(news_dir, "20260310", items) == 2
    assert append_news_items(news_dir, "20260310", items) == 0

    lines = (news_dir / "20260310.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


async def test_collect_news_for_date_with_retry_succeeds_after_retry():
    items = [NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="101500", title="A", dorg="KIS", tickers=("005930",))]
    fetch_result = NewsDisclosureFetchResult(items=items)
    kis = object()
    with patch("kindshot.collector._collect_news_fetch_result_for_date", new=AsyncMock(side_effect=[RuntimeError("boom"), fetch_result])) as mock_fetch, \
         patch("kindshot.collector.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        result = await _collect_news_for_date_with_retry(kis, "20260310", max_attempts=3, retry_delay_s=1.0)

    assert result == fetch_result
    assert mock_fetch.await_count == 2
    mock_sleep.assert_awaited_once_with(1.0)


async def test_collect_news_for_date_with_retry_raises_after_last_attempt():
    kis = object()
    with patch("kindshot.collector._collect_news_fetch_result_for_date", new=AsyncMock(side_effect=RuntimeError("boom"))) as mock_fetch, \
         patch("kindshot.collector.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        with pytest.raises(RuntimeError, match="boom"):
            await _collect_news_for_date_with_retry(kis, "20260310", max_attempts=2, retry_delay_s=1.5)

    assert mock_fetch.await_count == 2
    mock_sleep.assert_awaited_once_with(1.5)


async def test_collect_all_news_for_date_continues_after_pagination_truncation():
    kis = object()
    first = NewsDisclosureFetchResult(
        items=[
            NewsDisclosure(news_id="NEWS003", data_dt="20260310", data_tm="120000", title="C", dorg="KIS", tickers=("005930",)),
            NewsDisclosure(news_id="NEWS002", data_dt="20260310", data_tm="115500", title="B", dorg="KIS", tickers=("005930",)),
        ],
        pagination_truncated=True,
    )
    second = NewsDisclosureFetchResult(
        items=[
            NewsDisclosure(news_id="NEWS002", data_dt="20260310", data_tm="115500", title="B", dorg="KIS", tickers=("005930",)),
            NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="114000", title="A", dorg="KIS", tickers=("005930",)),
        ],
        pagination_truncated=False,
    )

    with patch("kindshot.collector._collect_news_for_date_with_retry", new=AsyncMock(return_value=first)) as first_fetch, \
         patch("kindshot.collector._collect_news_fetch_result_for_date_with_retry_window", new=AsyncMock(return_value=second)) as next_fetch:
        result = await _collect_all_news_for_date(kis, "20260310", max_attempts=3, retry_delay_s=1.0)

    assert [item.news_id for item in result.items] == ["NEWS003", "NEWS002", "NEWS001"]
    assert result.pagination_truncated is False
    first_fetch.assert_awaited_once()
    next_fetch.assert_awaited_once()
    assert next_fetch.await_args.kwargs["from_time"] == "115459"


async def test_collect_daily_index_prefers_kis_exact_rows():
    kis = AsyncMock()
    kis.get_index_daily_info = AsyncMock(
        side_effect=[
            type("IndexDailyInfo", (), {"open_px": 2500.0, "high": 2510.0, "low": 2490.0, "close": 2505.0, "volume": 1000.0, "value": 2000.0})(),
            type("IndexDailyInfo", (), {"open_px": 800.0, "high": 810.0, "low": 790.0, "close": 805.0, "volume": 3000.0, "value": 4000.0})(),
        ]
    )

    rows, source = await _collect_daily_index(kis, "20260313")

    assert [row["index_code"] for row in rows] == ["1001", "2001"]
    assert source == "kis"
    assert rows[0]["close"] == 2505.0
    assert rows[1]["close"] == 805.0


async def test_collect_daily_index_falls_back_to_pykrx_when_kis_empty():
    kis = AsyncMock()
    kis.get_index_daily_info = AsyncMock(side_effect=[None, None])
    pykrx_rows = [
        {
            "index_date": "1001:20260313",
            "index_code": "1001",
            "date": "20260313",
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
            "value": 20.0,
            "collected_at": "now",
        }
    ]

    with patch("kindshot.collector.asyncio.to_thread", new=AsyncMock(return_value=(pykrx_rows, "pykrx"))):
        rows, source = await _collect_daily_index(kis, "20260313")

    assert rows == pykrx_rows
    assert source == "pykrx"


async def test_run_backfill_collects_news_and_updates_state(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    items = [
        NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="101500", title="A", dorg="KIS", tickers=("005930",)),
        NewsDisclosure(news_id="NEWS002", data_dt="20260310", data_tm="101501", title="B", dorg="KIS", tickers=("000660",)),
    ]

    price_rows = [{"ticker_date": "005930:20260310", "ticker": "005930", "date": "20260310"}]
    index_rows = [{"index_date": "1001:20260310", "index_code": "1001", "date": "20260310"}]

    with patch("kindshot.collector.compute_finalized_date", return_value="20260310"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_news_for_date_with_retry", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=items))), \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=price_rows)), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=(index_rows, "pykrx"))):
        result = await run_backfill(cfg, cursor="20260310")

    assert result.processed_dates == ["20260310"]
    assert result.completed_dates == ["20260310"]
    assert result.partial_dates == []
    assert result.news_counts["20260310"] == 2
    assert result.classification_counts["20260310"] == 2
    assert result.price_counts["20260310"] == 1
    assert result.index_counts["20260310"] == 1

    state = load_collector_state(cfg.collector_state_path)
    assert state.last_completed_date == "20260310"
    assert state.finalized_date == "20260310"
    assert state.status == "idle"

    output_path = cfg.collector_news_dir / "20260310.jsonl"
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert [row["news_id"] for row in rows] == ["NEWS001", "NEWS002"]
    classification_rows = [json.loads(line) for line in (cfg.collector_classifications_dir / "20260310.jsonl").read_text(encoding="utf-8").splitlines()]
    assert classification_rows[0]["news_id"] == "NEWS001"
    assert "bucket" in classification_rows[0]
    assert (cfg.collector_daily_prices_dir / "20260310.jsonl").exists()
    assert (cfg.collector_index_dir / "20260310.jsonl").exists()
    manifest = json.loads((cfg.collector_manifests_dir / "20260310.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["status_reason"] == ""
    assert manifest["has_partial_data"] is False
    assert manifest["counts"]["news"] == 2
    assert manifest["counts"]["classifications"] == 2
    assert manifest["tickers"] == ["000660", "005930"]
    assert manifest["news_range"]["first_news_id"] == "NEWS001"
    assert manifest["news_range"]["last_news_id"] == "NEWS002"
    assert manifest["exists"]["news"] is True
    assert manifest["exists"]["classifications"] is True
    assert manifest["sources"]["daily_index"] == "pykrx"
    index_payload = json.loads((cfg.collector_manifests_dir / "index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["date"] == "20260310"
    assert index_payload["entries"][0]["status"] == "complete"
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["status"] == "complete"
    assert log_rows[-1]["news_count"] == 2
    assert log_rows[-1]["classification_count"] == 2


async def test_run_backfill_from_only_collects_finalized_through_from_date(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260313"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_news_for_date_with_retry", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=[]))) as mock_news, \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([{"index_date": "1001:20260313"}], "pykrx"))):
        result = await run_backfill(cfg, from_date="20260312")

    assert result.processed_dates == ["20260313", "20260312"]
    assert result.completed_dates == ["20260313", "20260312"]
    assert result.partial_dates == []
    assert mock_news.await_count == 2


async def test_run_backfill_skips_dates_already_marked_complete(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260313",
            status="complete",
            news_count=2,
            classification_count=2,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260313",
        status="complete",
        status_reason="",
        finalized_date="20260313",
        items=[NewsDisclosure(news_id="NEWS001", data_dt="20260313", data_tm="101500", title="A", dorg="KIS", tickers=("005930",))],
        tickers=["005930"],
        news_count=2,
        classification_count=2,
        price_count=1,
        index_count=1,
        news_path=cfg.collector_news_dir / "20260313.jsonl",
        classifications_path=cfg.collector_classifications_dir / "20260313.jsonl",
        daily_prices_path=cfg.collector_daily_prices_dir / "20260313.jsonl",
        daily_index_path=cfg.collector_index_dir / "20260313.jsonl",
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260313"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_news_for_date_with_retry", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=[]))) as mock_news, \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([], "pykrx"))):
        result = await run_backfill(cfg, cursor="20260313")

    assert result.processed_dates == []
    assert result.completed_dates == []
    assert result.partial_dates == []
    assert result.skipped_dates == ["20260313"]
    assert mock_news.await_count == 0
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["date"] == "20260313"
    assert log_rows[-1]["status"] == "skipped"
    assert log_rows[-1]["skip_reason"] == "already_complete"
    state = load_collector_state(cfg.collector_state_path)
    assert state.cursor_date == "20260312"
    assert state.status == "idle"


async def test_run_backfill_reprocesses_stale_complete_without_manifest(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    save_collector_state(
        cfg.collector_state_path,
        CollectorState(cursor_date="20260315", last_completed_date="20260315", finalized_date="20260315", status="idle"),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260315",
            status="complete",
            news_count=40,
            classification_count=40,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-16T16:00:00+09:00",
        ),
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260315"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=False)), \
         patch("kindshot.collector._collect_all_news_for_date", new=AsyncMock()) as mock_news:
        result = await run_backfill(cfg, cursor="20260315")

    assert result.processed_dates == []
    assert result.completed_dates == []
    assert result.partial_dates == []
    assert result.skipped_dates == ["20260315"]
    assert mock_news.await_count == 0
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["status"] == "skipped"
    assert log_rows[-1]["skip_reason"] == "non_trading_day"
    state = load_collector_state(cfg.collector_state_path)
    assert state.cursor_date == "20260314"
    assert state.last_completed_date == ""


async def test_run_backfill_logs_partial_when_news_pagination_truncated(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    items = [NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="101500", title="A", dorg="KIS", tickers=("005930",))]

    with patch("kindshot.collector.compute_finalized_date", return_value="20260310"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_all_news_for_date", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=items, pagination_truncated=True))), \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([], "pykrx"))):
        result = await run_backfill(cfg, cursor="20260310")

    assert result.processed_dates == ["20260310"]
    assert result.completed_dates == []
    assert result.partial_dates == ["20260310"]
    manifest = json.loads((cfg.collector_manifests_dir / "20260310.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["status_reason"] == "pagination_truncated,daily_prices_missing,daily_index_missing"
    assert manifest["has_partial_data"] is True
    assert manifest["counts"]["news"] == 1
    assert manifest["news_range"]["first_news_id"] == "NEWS001"
    assert manifest["news_range"]["end_time"] == "101500"
    index_payload = json.loads((cfg.collector_manifests_dir / "index.json").read_text(encoding="utf-8"))
    assert index_payload["entries"][0]["date"] == "20260310"
    assert index_payload["entries"][0]["status"] == "partial"
    assert index_payload["entries"][0]["has_partial_data"] is True
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["status"] == "partial"
    assert log_rows[-1]["skip_reason"] == "pagination_truncated,daily_prices_missing,daily_index_missing"
    state = load_collector_state(cfg.collector_state_path)
    assert state.cursor_date == "20260310"
    assert state.last_completed_date == ""


async def test_run_backfill_does_not_skip_latest_partial_date(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=5,
            classification_count=5,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated",
        ),
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260310"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_news_for_date_with_retry", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=[]))) as mock_news, \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[{"ticker_date": "005930:20260310"}])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([{"index_date": "1001:20260310"}], "pykrx"))):
        result = await run_backfill(cfg, cursor="20260310")

    assert result.processed_dates == ["20260310"]
    assert result.completed_dates == ["20260310"]
    assert result.partial_dates == []
    assert mock_news.await_count == 1


async def test_run_backfill_skips_non_trading_day(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260315"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=False)), \
         patch("kindshot.collector._collect_all_news_for_date", new=AsyncMock()) as mock_news:
        result = await run_backfill(cfg, cursor="20260315")

    assert result.processed_dates == []
    assert result.completed_dates == []
    assert result.partial_dates == []
    assert result.skipped_dates == ["20260315"]
    assert mock_news.await_count == 0
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["status"] == "skipped"
    assert log_rows[-1]["skip_reason"] == "non_trading_day"
    state = load_collector_state(cfg.collector_state_path)
    assert state.cursor_date == "20260314"
    assert state.last_completed_date == ""


async def test_run_backfill_marks_partial_when_trading_day_price_and_index_missing(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    items = [NewsDisclosure(news_id="NEWS001", data_dt="20260310", data_tm="101500", title="A", dorg="KIS", tickers=("005930",))]

    with patch("kindshot.collector.compute_finalized_date", return_value="20260310"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_all_news_for_date", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=items))), \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([], "pykrx"))):
        result = await run_backfill(cfg, cursor="20260310")

    assert result.completed_dates == []
    assert result.partial_dates == ["20260310"]
    manifest = json.loads((cfg.collector_manifests_dir / "20260310.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["status_reason"] == "daily_prices_missing,daily_index_missing"
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["skip_reason"] == "daily_prices_missing,daily_index_missing"


async def test_run_backfill_allows_no_news_day_without_index_backlog(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260310"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_all_news_for_date", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=[]))), \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([], "pykrx"))):
        result = await run_backfill(cfg, cursor="20260310")

    assert result.completed_dates == ["20260310"]
    assert result.partial_dates == []
    manifest = json.loads((cfg.collector_manifests_dir / "20260310.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["status_reason"] == ""
    log_rows = [json.loads(line) for line in cfg.collector_log_path.read_text(encoding="utf-8").splitlines()]
    assert log_rows[-1]["status"] == "complete"
    assert log_rows[-1]["skip_reason"] == ""


async def test_run_backfill_reuses_existing_price_rows_on_retry(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_daily_prices_dir=tmp_path / "data" / "collector" / "daily_prices",
        collector_index_dir=tmp_path / "data" / "collector" / "index",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    items = [NewsDisclosure(news_id="NEWS001", data_dt="20260313", data_tm="101500", title="A", dorg="KIS", tickers=("005930",))]
    existing_price_path = cfg.collector_daily_prices_dir / "20260313.jsonl"
    existing_price_path.parent.mkdir(parents=True, exist_ok=True)
    existing_price_path.write_text(
        json.dumps({"ticker_date": "005930:20260313", "ticker": "005930", "date": "20260313"}) + "\n",
        encoding="utf-8",
    )

    with patch("kindshot.collector.compute_finalized_date", return_value="20260313"), \
         patch("kindshot.collector._is_market_business_day", new=AsyncMock(return_value=True)), \
         patch("kindshot.collector._collect_all_news_for_date", new=AsyncMock(return_value=NewsDisclosureFetchResult(items=items))), \
         patch("kindshot.collector._collect_daily_prices", new=AsyncMock(return_value=[{"ticker_date": "005930:20260313", "ticker": "005930", "date": "20260313"}])), \
         patch("kindshot.collector._collect_daily_index", new=AsyncMock(return_value=([], "pykrx"))):
        result = await run_backfill(cfg, cursor="20260313")

    assert result.partial_dates == ["20260313"]
    assert result.price_counts["20260313"] == 1
    manifest = json.loads((cfg.collector_manifests_dir / "20260313.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["daily_prices"] == 1
    assert manifest["status_reason"] == "daily_index_missing"


async def test_collect_main_dispatches_backfill(tmp_path):
    cfg = Config(
        collector_news_dir=tmp_path / "data" / "collector" / "news",
        collector_classifications_dir=tmp_path / "data" / "collector" / "classifications",
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    fake_result = AsyncMock()
    fake_result.requested_from = "20260310"
    fake_result.requested_to = "20260310"
    fake_result.finalized_date = "20260310"
    fake_result.processed_dates = ["20260310"]
    fake_result.completed_dates = ["20260310"]
    fake_result.partial_dates = []
    fake_result.skipped_dates = []
    with patch("kindshot.collector.run_backfill", new=AsyncMock(return_value=fake_result)) as mock_run, \
         patch("kindshot.collector.logger.info") as mock_info:
        await collect_main(["backfill", "--cursor", "20260310"], cfg)

    mock_run.assert_awaited_once()
    assert mock_run.await_args.kwargs["cursor"] == "20260310"
    mock_info.assert_called_once()
    assert "complete=%d partial=%d skipped=%d" in mock_info.call_args.args[0]


def test_log_collection_status_reports_backlog_with_limit(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    save_collector_state(
        cfg.collector_state_path,
        CollectorState(
            cursor_date="20260310",
            last_completed_date="20260311",
            finalized_date="20260312",
            status="idle",
        ),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated",
        ),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260309",
            status="partial",
            news_count=1,
            classification_count=1,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:30+09:00",
            skip_reason="pagination_truncated",
        ),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260308",
            status="error",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:01:00+09:00",
            error="boom",
        ),
    )

    with patch("kindshot.collector.logger.info") as mock_info:
        summary = log_collection_status(cfg, backlog_limit=1)

    assert summary.partial_dates == ["20260310", "20260309"]
    assert summary.error_dates == ["20260308"]
    assert summary.oldest_partial_date == "20260309"
    assert summary.oldest_error_date == "20260308"
    assert summary.oldest_blocked_date == "20260308"
    assert summary.blocked_news_count == 4
    assert summary.blocked_classification_count == 4
    assert summary.blocked_price_count == 2
    assert summary.blocked_index_count == 4
    assert mock_info.call_count == 5
    assert "tracked=%d partial=%d error=%d oldest_partial=%s oldest_error=%s oldest_blocked=%s oldest_blocked_age_s=%d blocked_news=%d blocked_classified=%d blocked_prices=%d blocked_index=%d" in mock_info.call_args_list[0].args[0]
    assert mock_info.call_args_list[0].args[6] == 3
    assert mock_info.call_args_list[0].args[7] == 2
    assert mock_info.call_args_list[0].args[8] == 1
    assert mock_info.call_args_list[0].args[9] == "20260309"
    assert mock_info.call_args_list[0].args[10] == "20260308"
    assert mock_info.call_args_list[0].args[11] == "20260308"
    assert mock_info.call_args_list[0].args[12] >= 0
    assert mock_info.call_args_list[0].args[13] == 4
    assert mock_info.call_args_list[0].args[14] == 4
    assert mock_info.call_args_list[0].args[15] == 2
    assert mock_info.call_args_list[0].args[16] == 4
    assert mock_info.call_args_list[1].args[1] == 1
    assert mock_info.call_args_list[1].args[2] == "20260310"
    assert mock_info.call_args_list[2].args[1] == "20260310"
    assert mock_info.call_args_list[2].args[2] == "pagination_truncated"
    assert mock_info.call_args_list[3].args[1] == 1
    assert mock_info.call_args_list[3].args[2] == "20260308"
    assert mock_info.call_args_list[4].args[1] == "20260308"
    assert mock_info.call_args_list[4].args[2] == "boom"


def test_build_status_report_returns_limited_machine_readable_payload(tmp_path):
    state = CollectorState(
        cursor_date="20260310",
        last_completed_date="20260311",
        finalized_date="20260312",
        status="idle",
        updated_at="2026-03-15T00:05:00+09:00",
    )
    summary = CollectionLogSummary(
        latest_statuses={"20260310": "partial", "20260308": "error"},
        latest_records={
            "20260310": CollectionLogRecord(
                date="20260310",
                status="partial",
                news_count=3,
                classification_count=3,
                daily_price_count=1,
                daily_index_count=2,
                completed_at="2026-03-15T00:00:00+09:00",
                skip_reason="pagination_truncated",
            ),
            "20260308": CollectionLogRecord(
                date="20260308",
                status="error",
                news_count=0,
                classification_count=0,
                daily_price_count=0,
                daily_index_count=0,
                completed_at="2026-03-15T00:01:00+09:00",
                error="boom",
            ),
        },
        partial_dates=["20260310"],
        error_dates=["20260308"],
        tracked_dates=["20260310", "20260308"],
        oldest_partial_date="20260310",
        oldest_error_date="20260308",
        oldest_blocked_date="20260308",
        blocked_news_count=3,
        blocked_classification_count=3,
        blocked_price_count=1,
        blocked_index_count=2,
        status_generated_at="2026-03-15T00:05:00+09:00",
        oldest_blocked_age_seconds=240,
    )

    report = _build_status_report(state, summary, backlog_limit=1)

    assert report["state"]["cursor_date"] == "20260310"
    assert report["summary"]["health"] == "error_backlog"
    assert report["summary"]["status_generated_at"] == "2026-03-15T00:05:00+09:00"
    assert report["summary"]["oldest_blocked_age_seconds"] == 240
    assert report["summary"]["blocked_news_count"] == 3
    assert report["backlog"]["limit"] == 1
    assert report["backlog"]["partial_dates"] == ["20260310"]
    assert report["backlog"]["error_dates"] == ["20260308"]
    assert report["backlog"]["partial_details"][0]["skip_reason"] == "pagination_truncated"
    assert report["backlog"]["error_details"][0]["error"] == "boom"


def test_build_status_detail_reads_manifest_context(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    write_collection_day_manifest(
        manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="pagination_truncated,daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=["005930"],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    detail = _build_status_detail(
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated,daily_index_missing",
        ),
        collector_manifests_dir=manifests_dir,
    )

    assert detail["manifest_exists"] is True
    assert detail["manifest_status"] == "partial"
    assert detail["manifest_has_partial_data"] is True
    assert detail["manifest_status_reason"] == "pagination_truncated,daily_index_missing"
    assert detail["manifest_path"].endswith("20260310.json")


def test_build_status_detail_falls_back_from_stale_index_path(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    write_collection_day_manifest(
        manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=1,
        classification_count=1,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    detail = _build_status_detail(
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=1,
            classification_count=1,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="daily_index_missing",
        ),
        collector_manifests_dir=manifests_dir,
        manifest_index_entry=CollectionManifestIndexEntry(
            date="20260310",
            status="partial",
            has_partial_data=True,
            generated_at="2026-03-15T00:00:00+09:00",
            manifest_path=str(tmp_path / "stale" / "20260310.json"),
        ),
    )

    assert detail["manifest_exists"] is True
    assert detail["manifest_status"] == "partial"
    assert detail["manifest_status_reason"] == "daily_index_missing"
    assert detail["manifest_path"].endswith("data/collector/manifests/20260310.json")


def test_build_status_report_includes_manifest_context_for_backlog_details(tmp_path):
    manifests_dir = tmp_path / "data" / "collector" / "manifests"
    write_collection_day_manifest(
        manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="pagination_truncated",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=2,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    report = _build_status_report(
        CollectorState(status="idle", cursor_date="20260310"),
        CollectionLogSummary(
            latest_statuses={"20260310": "partial"},
            latest_records={
                "20260310": CollectionLogRecord(
                    date="20260310",
                    status="partial",
                    news_count=3,
                    classification_count=3,
                    daily_price_count=1,
                    daily_index_count=2,
                    completed_at="2026-03-15T00:00:00+09:00",
                    skip_reason="pagination_truncated",
                ),
            },
            partial_dates=["20260310"],
            error_dates=[],
            tracked_dates=["20260310"],
            oldest_partial_date="20260310",
            oldest_error_date="",
            oldest_blocked_date="20260310",
            blocked_news_count=3,
            blocked_classification_count=3,
            blocked_price_count=1,
            blocked_index_count=2,
            status_generated_at="2026-03-15T00:05:00+09:00",
            oldest_blocked_age_seconds=240,
        ),
        backlog_limit=1,
        collector_manifests_dir=manifests_dir,
    )

    assert report["backlog"]["partial_details"][0]["manifest_status"] == "partial"
    assert report["backlog"]["partial_details"][0]["manifest_status_reason"] == "pagination_truncated"
    assert report["backlog"]["partial_details"][0]["manifest_path"].endswith("20260310.json")


def test_compute_status_health_prefers_collector_error_then_error_then_partial():
    summary = CollectionLogSummary({}, {}, [], [], [], "", "", "", 0, 0, 0, 0, "", 0)
    assert _compute_status_health(CollectorState(status="error"), summary) == "collector_error"
    assert _compute_status_health(CollectorState(status="idle"), CollectionLogSummary({}, {}, [], ["20260308"], [], "", "20260308", "20260308", 0, 0, 0, 0, "", 0)) == "error_backlog"
    assert _compute_status_health(CollectorState(status="idle"), CollectionLogSummary({}, {}, ["20260310"], [], [], "20260310", "", "20260310", 0, 0, 0, 0, "", 0)) == "partial_backlog"
    assert _compute_status_health(CollectorState(status="idle"), summary) == "healthy"


def test_print_collection_status_json_emits_report_and_writes_file(tmp_path, capsys):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
    )
    output_path = tmp_path / "data" / "collector" / "status.json"
    save_collector_state(
        cfg.collector_state_path,
        CollectorState(
            cursor_date="20260310",
            last_completed_date="20260311",
            finalized_date="20260312",
            status="idle",
        ),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=2,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="pagination_truncated",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=2,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )

    report = print_collection_status_json(cfg, backlog_limit=1, output_path=str(output_path))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert report == payload
    assert payload == file_payload
    assert payload["summary"]["health"] == "partial_backlog"
    assert payload["summary"]["status_generated_at"]
    assert payload["summary"]["oldest_blocked_age_seconds"] >= 0
    assert payload["summary"]["partial_count"] == 1
    assert payload["backlog"]["partial_dates"] == ["20260310"]
    assert payload["backlog"]["partial_details"][0]["manifest_status"] == "partial"
    assert payload["backlog"]["partial_details"][0]["manifest_status_reason"] == "pagination_truncated"
    assert payload["backlog"]["partial_details"][0]["manifest_path"].endswith("20260310.json")


def test_load_collection_status_report_reuses_state_summary_and_manifest_context(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
    )
    state = CollectorState(
        cursor_date="20260310",
        last_completed_date="20260311",
        finalized_date="20260312",
        status="idle",
    )
    save_collector_state(cfg.collector_state_path, state)
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated,daily_index_missing",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="pagination_truncated,daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    summary = load_collection_log_summary(cfg.collector_log_path)

    report = load_collection_status_report(cfg, backlog_limit=1, state=state, summary=summary)

    assert report["state"]["cursor_date"] == "20260310"
    assert report["summary"]["health"] == "partial_backlog"
    assert report["backlog"]["partial_details"][0]["manifest_status_reason"] == "pagination_truncated,daily_index_missing"
    assert report["backlog"]["partial_details"][0]["manifest_path"].endswith("20260310.json")


def test_build_collection_backfill_report_includes_rows_and_collector_status(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
    )
    state = CollectorState(
        cursor_date="20260310",
        last_completed_date="20260311",
        finalized_date="20260312",
        status="idle",
    )
    save_collector_state(cfg.collector_state_path, state)
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated,daily_index_missing",
        ),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260309",
            status="skipped",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:01:00+09:00",
            skip_reason="already_complete",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="pagination_truncated,daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    result = BackfillResult(
        requested_from="20260310",
        requested_to="20260309",
        finalized_date="20260312",
        processed_dates=["20260310"],
        completed_dates=[],
        partial_dates=["20260310"],
        news_counts={},
        classification_counts={},
        price_counts={},
        index_counts={},
        skipped_dates=["20260309"],
    )

    report = build_collection_backfill_report(
        cfg,
        cursor="20260310",
        result=result,
        state=state,
        summary=load_collection_log_summary(cfg.collector_log_path),
    )

    assert report["request"]["cursor"] == "20260310"
    assert report["result"]["requested_from"] == "20260310"
    assert report["result"]["partial_count"] == 1
    assert report["collector_status"]["summary"]["health"] == "partial_backlog"
    assert report["rows"][0]["date"] == "20260310"
    assert report["rows"][0]["processed_by_run"] is True
    assert report["rows"][0]["manifest_status_reason"] == "pagination_truncated,daily_index_missing"
    assert report["rows"][1]["date"] == "20260309"
    assert report["rows"][1]["skipped_by_run"] is True


def test_print_collection_backfill_json_emits_report_and_writes_file(tmp_path, capsys):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
    )
    output_path = tmp_path / "data" / "collector" / "backfill.json"
    state = CollectorState(
        cursor_date="20260310",
        last_completed_date="20260311",
        finalized_date="20260312",
        status="idle",
    )
    save_collector_state(cfg.collector_state_path, state)
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="daily_index_missing",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    result = BackfillResult(
        requested_from="20260310",
        requested_to="20260310",
        finalized_date="20260312",
        processed_dates=["20260310"],
        completed_dates=[],
        partial_dates=["20260310"],
        news_counts={},
        classification_counts={},
        price_counts={},
        index_counts={},
        skipped_dates=[],
    )

    report = print_collection_backfill_json(
        cfg,
        cursor="20260310",
        result=result,
        output_path=str(output_path),
        state=state,
        summary=load_collection_log_summary(cfg.collector_log_path),
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    file_payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert report == payload
    assert payload == file_payload
    assert payload["result"]["partial_dates"] == ["20260310"]
    assert payload["collector_status"]["summary"]["health"] == "partial_backlog"
    assert payload["rows"][0]["manifest_path"].endswith("20260310.json")


def test_write_collection_backfill_report_writes_default_latest_path(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_backfill_report_path=tmp_path / "data" / "collector" / "backfill" / "latest.json",
    )
    state = CollectorState(
        cursor_date="20260310",
        last_completed_date="20260311",
        finalized_date="20260312",
        status="idle",
    )
    save_collector_state(cfg.collector_state_path, state)
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="daily_index_missing",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )
    result = BackfillResult(
        requested_from="20260310",
        requested_to="20260310",
        finalized_date="20260312",
        processed_dates=["20260310"],
        completed_dates=[],
        partial_dates=["20260310"],
        news_counts={},
        classification_counts={},
        price_counts={},
        index_counts={},
        skipped_dates=[],
    )

    report, path = write_collection_backfill_report(
        cfg,
        cursor="20260310",
        result=result,
        state=state,
        summary=load_collection_log_summary(cfg.collector_log_path),
    )

    assert path == cfg.collector_backfill_report_path
    file_payload = json.loads(path.read_text(encoding="utf-8"))
    assert file_payload == report
    assert file_payload["result"]["processed_dates"] == ["20260310"]


def test_print_collection_backfill_json_uses_default_latest_path_when_output_missing(tmp_path, capsys):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
        collector_backfill_report_path=tmp_path / "data" / "collector" / "backfill" / "latest.json",
    )
    state = CollectorState(cursor_date="20260310", last_completed_date="", finalized_date="20260312", status="idle")
    save_collector_state(cfg.collector_state_path, state)
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="error",
            news_count=0,
            classification_count=0,
            daily_price_count=0,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            error="boom",
        ),
    )

    report = print_collection_backfill_json(
        cfg,
        cursor="20260310",
        error=RuntimeError("boom"),
        state=state,
        summary=load_collection_log_summary(cfg.collector_log_path),
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    file_payload = json.loads(cfg.collector_backfill_report_path.read_text(encoding="utf-8"))

    assert report == payload
    assert payload == file_payload
    assert payload["error"]["message"] == "boom"


def test_log_collection_status_logs_manifest_context(tmp_path, caplog):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
        collector_manifests_dir=tmp_path / "data" / "collector" / "manifests",
    )
    save_collector_state(
        cfg.collector_state_path,
        CollectorState(cursor_date="20260310", last_completed_date="20260311", finalized_date="20260312", status="idle"),
    )
    append_collection_log(
        cfg.collector_log_path,
        CollectionLogRecord(
            date="20260310",
            status="partial",
            news_count=3,
            classification_count=3,
            daily_price_count=1,
            daily_index_count=0,
            completed_at="2026-03-15T00:00:00+09:00",
            skip_reason="pagination_truncated,daily_index_missing",
        ),
    )
    write_collection_day_manifest(
        cfg.collector_manifests_dir,
        dt="20260310",
        status="partial",
        status_reason="pagination_truncated,daily_index_missing",
        finalized_date="20260312",
        items=[],
        tickers=[],
        news_count=3,
        classification_count=3,
        price_count=1,
        index_count=0,
        news_path=tmp_path / "news" / "20260310.jsonl",
        classifications_path=tmp_path / "classifications" / "20260310.jsonl",
        daily_prices_path=tmp_path / "prices" / "20260310.jsonl",
        daily_index_path=tmp_path / "index" / "20260310.jsonl",
    )

    with caplog.at_level(logging.INFO):
        log_collection_status(cfg, backlog_limit=1)

    assert "Collect status partial detail" in caplog.text
    assert "manifest_reason=pagination_truncated,daily_index_missing" in caplog.text
    assert "manifest_status=partial" in caplog.text
    assert "manifest=" in caplog.text


async def test_collect_main_dispatches_status(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )

    with patch(
        "kindshot.collector.log_collection_status",
        return_value=CollectionLogSummary({}, {}, [], [], [], "", "", "", 0, 0, 0, 0, "", 0),
    ) as mock_status:
        await collect_main(["status", "--limit", "5"], cfg)

    mock_status.assert_called_once_with(cfg, backlog_limit=5)


async def test_collect_main_dispatches_status_json(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )

    with patch("kindshot.collector.print_collection_status_json", return_value={"summary": {}}) as mock_status:
        await collect_main(["status", "--limit", "5", "--json", "--output", "status.json"], cfg)

    mock_status.assert_called_once_with(cfg, backlog_limit=5, output_path="status.json")


async def test_collect_main_dispatches_backfill_json(tmp_path):
    cfg = Config(
        collector_log_path=tmp_path / "data" / "collector" / "collection_log.jsonl",
        collector_state_path=tmp_path / "data" / "collector_state.json",
    )
    fake_result = BackfillResult(
        requested_from="20260310",
        requested_to="20260310",
        finalized_date="20260310",
        processed_dates=["20260310"],
        completed_dates=["20260310"],
        partial_dates=[],
        news_counts={},
        classification_counts={},
        price_counts={},
        index_counts={},
        skipped_dates=[],
    )

    with patch("kindshot.collector.run_backfill", new=AsyncMock(return_value=fake_result)) as mock_run, \
         patch("kindshot.collector.print_collection_backfill_json", return_value={"result": {}}) as mock_report:
        await collect_main(["backfill", "--cursor", "20260310", "--json", "--output", "backfill.json"], cfg)

    mock_run.assert_awaited_once()
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["cursor"] == "20260310"
    assert mock_report.call_args.kwargs["output_path"] == "backfill.json"
    assert mock_report.call_args.kwargs["result"] is fake_result
