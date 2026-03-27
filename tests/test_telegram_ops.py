import json

from kindshot.collector import BackfillResult, CollectionLogRecord, CollectionLogSummary, CollectorState
from kindshot.telegram_ops import _sanitize_path, format_backfill_notification, send_telegram_message


def _summary() -> CollectionLogSummary:
    return CollectionLogSummary(
        latest_statuses={},
        latest_records={
            "20260316": CollectionLogRecord(
                date="20260316",
                status="partial",
                news_count=40,
                classification_count=40,
                daily_price_count=2,
                daily_index_count=0,
                completed_at="2026-03-16T16:40:00+09:00",
                error="",
                skip_reason="daily_index_missing",
            ),
            "20260315": CollectionLogRecord(
                date="20260315",
                status="skipped",
                news_count=0,
                classification_count=0,
                daily_price_count=0,
                daily_index_count=0,
                completed_at="2026-03-16T16:39:00+09:00",
                error="",
                skip_reason="non_trading_day",
            ),
            "20260314": CollectionLogRecord(
                date="20260314",
                status="error",
                news_count=0,
                classification_count=0,
                daily_price_count=0,
                daily_index_count=0,
                completed_at="2026-03-16T16:38:00+09:00",
                error="boom",
                skip_reason="",
            ),
        },
        partial_dates=["20260316"],
        error_dates=["20260314"],
        tracked_dates=["20260316", "20260315", "20260314"],
        oldest_partial_date="20260316",
        oldest_error_date="20260314",
        oldest_blocked_date="20260314",
        blocked_news_count=12,
        blocked_classification_count=12,
        blocked_price_count=4,
        blocked_index_count=2,
        status_generated_at="2026-03-16T16:40:00+09:00",
        oldest_blocked_age_seconds=300,
    )


def _status_report() -> dict:
    return {
        "summary": {
            "health": "error_backlog",
            "oldest_blocked_age_seconds": 300,
        },
        "backlog": {
            "partial_details": [
                {
                    "date": "20260316",
                    "skip_reason": "daily_index_missing",
                    "manifest_status_reason": "daily_index_missing",
                    "manifest_status": "partial",
                    "manifest_path": "data/collector/manifests/20260316.json",
                }
            ],
            "error_details": [
                {
                    "date": "20260314",
                    "error": "boom",
                    "manifest_status_reason": "daily_prices_missing",
                    "manifest_status": "partial",
                    "manifest_path": "data/collector/manifests/20260314.json",
                }
            ],
        },
    }


def test_format_backfill_notification_success():
    result = BackfillResult(
        requested_from="20260315",
        requested_to="20260315",
        finalized_date="20260315",
        processed_dates=["20260315"],
        completed_dates=["20260315"],
        partial_dates=[],
        news_counts={"20260315": 10},
        classification_counts={"20260315": 10},
        price_counts={"20260315": 4},
        index_counts={"20260315": 2},
        skipped_dates=[],
    )
    state = CollectorState(status="idle", cursor_date="20260314", last_completed_date="20260315")

    text = format_backfill_notification(
        result,
        state,
        _summary(),
        status_report=_status_report(),
        report_paths={"backfill_report": "data/collector/backfill/latest.json"},
    )

    assert "Kindshot Backfill OK" in text
    assert "range=20260315->20260315 finalized=20260315" in text
    assert "processed=1 complete=1 partial=0 skipped=0" in text
    assert "backfill_report=data/collector/backfill/latest.json" in text
    assert "collector=idle cursor=20260314 last_completed=20260315" in text
    assert "backlog_health=error_backlog oldest_blocked_age_s=300" in text


def test_format_backfill_notification_includes_manifest_aware_partial_and_error_details():
    result = BackfillResult(
        requested_from="20260316",
        requested_to="20260314",
        finalized_date="20260316",
        processed_dates=["20260316"],
        completed_dates=[],
        partial_dates=["20260316"],
        news_counts={"20260316": 40},
        classification_counts={"20260316": 40},
        price_counts={"20260316": 0},
        index_counts={"20260316": 0},
        skipped_dates=["20260315"],
    )
    state = CollectorState(status="idle", cursor_date="20260316", last_completed_date="")

    text = format_backfill_notification(result, state, _summary(), status_report=_status_report())

    assert "partial_dates=20260316" in text
    assert "partial_reasons=20260316:daily_index_missing" in text
    assert "partial_detail=20260316 reason=daily_index_missing manifest_status=partial manifest=data/collector/manifests/20260316.json" in text
    assert "skipped_dates=20260315" in text
    assert "skip_reasons=20260315:non_trading_day" in text
    assert "error_detail=20260314 error=boom manifest_reason=daily_prices_missing manifest_status=partial manifest=data/collector/manifests/20260314.json" in text


def test_format_backfill_notification_failure():
    state = CollectorState(status="error", cursor_date="20260315", last_completed_date="20260314")

    text = format_backfill_notification(
        None,
        state,
        _summary(),
        error=RuntimeError("boom"),
        status_report=_status_report(),
        report_paths={
            "backfill_report": "data/collector/backfill/latest.json",
            "auto_report": "data/collector/backfill/auto_latest.json",
        },
    )

    assert "Kindshot Backfill FAIL" in text
    assert "backfill_report=data/collector/backfill/latest.json" in text
    assert "auto_report=data/collector/backfill/auto_latest.json" in text
    assert "error=RuntimeError: boom" in text
    assert "collector=error cursor=20260315 last_completed=20260314" in text
    assert "error_detail=20260314 error=boom manifest_reason=daily_prices_missing manifest_status=partial manifest=data/collector/manifests/20260314.json" in text


def test_sanitize_path_strips_absolute_prefix():
    assert _sanitize_path("/opt/kindshot/data/collector/backfill/latest.json") == "data/collector/backfill/latest.json"
    assert _sanitize_path("/home/user/app/logs/state/paper") == "logs/state/paper"
    assert _sanitize_path("data/collector/backfill/latest.json") == "data/collector/backfill/latest.json"
    assert _sanitize_path("/unknown/path/report.json") == "report.json"
    assert _sanitize_path("") == ""


def test_format_backfill_notification_sanitizes_absolute_paths():
    state = CollectorState(status="idle", cursor_date="20260315", last_completed_date="20260314")
    text = format_backfill_notification(
        None,
        state,
        _summary(),
        report_paths={
            "backfill_report": "/opt/kindshot/data/collector/backfill/latest.json",
            "auto_report": "/opt/kindshot/data/collector/backfill/auto_latest.json",
        },
    )
    assert "/opt/kindshot/" not in text
    assert "backfill_report=data/collector/backfill/latest.json" in text
    assert "auto_report=data/collector/backfill/auto_latest.json" in text


def test_send_telegram_message_builds_request(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("kindshot.telegram_ops.urlopen", _fake_urlopen)

    ok = send_telegram_message("hello", "token123", "chat456", timeout_s=3.5)

    assert ok is True
    assert captured["url"].endswith("/bottoken123/sendMessage")
    assert captured["body"]["chat_id"] == "chat456"
    assert captured["body"]["text"] == "hello"
    assert captured["timeout"] == 3.5
