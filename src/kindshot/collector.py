"""Historical collection entrypoints and state handling."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

from kindshot.config import Config
from kindshot.bucket import classify
from kindshot.kis_client import KisClient, NewsDisclosure, NewsDisclosureFetchResult

from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


@dataclass
class CollectorState:
    command: str = "collect"
    task: str = "backfill"
    cursor_date: str = ""
    last_completed_date: str = ""
    finalized_date: str = ""
    status: str = "idle"
    updated_at: str = ""
    last_error: str = ""


@dataclass
class BackfillResult:
    requested_from: str
    requested_to: str
    finalized_date: str
    processed_dates: list[str]
    completed_dates: list[str]
    partial_dates: list[str]
    news_counts: dict[str, int]
    classification_counts: dict[str, int]
    price_counts: dict[str, int]
    index_counts: dict[str, int]
    skipped_dates: list[str]


@dataclass
class CollectionLogRecord:
    date: str
    status: str
    news_count: int
    classification_count: int
    daily_price_count: int
    daily_index_count: int
    completed_at: str
    error: str = ""
    skip_reason: str = ""


@dataclass
class CollectionDayManifest:
    date: str
    status: str
    status_reason: str
    has_partial_data: bool
    finalized_date: str
    generated_at: str
    tickers: list[str]
    counts: dict[str, int]
    paths: dict[str, str]
    news_range: dict[str, str]
    sources: dict[str, str]
    exists: dict[str, bool]


@dataclass
class CollectionManifestIndexEntry:
    date: str
    status: str
    has_partial_data: bool
    generated_at: str
    manifest_path: str


@dataclass
class CollectionLogSummary:
    latest_statuses: dict[str, str]
    latest_records: dict[str, CollectionLogRecord]
    partial_dates: list[str]
    error_dates: list[str]
    tracked_dates: list[str]
    oldest_partial_date: str
    oldest_error_date: str
    oldest_blocked_date: str
    blocked_news_count: int
    blocked_classification_count: int
    blocked_price_count: int
    blocked_index_count: int
    status_generated_at: str
    oldest_blocked_age_seconds: int


def _kst_now() -> datetime:
    return datetime.now(_KST)


def _parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _format_yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _decrement_hhmmss(value: str) -> str:
    parsed = datetime.strptime(value, "%H%M%S")
    decremented = parsed - timedelta(seconds=1)
    if decremented.date() < parsed.date():
        return ""
    return decremented.strftime("%H%M%S")


def compute_finalized_date(
    now: Optional[datetime] = None,
    *,
    cutoff_hour: int = 2,
    cutoff_minute: int = 30,
) -> str:
    now_kst = (now or _kst_now()).astimezone(_KST)
    cutoff = time(hour=cutoff_hour, minute=cutoff_minute)
    delta_days = 2 if now_kst.time() < cutoff else 1
    return _format_yyyymmdd(now_kst.date() - timedelta(days=delta_days))


def load_collector_state(path: Path) -> CollectorState:
    if not path.exists():
        return CollectorState()
    data = json.loads(path.read_text(encoding="utf-8"))
    return CollectorState(
        command=str(data.get("command", "collect")),
        task=str(data.get("task", "backfill")),
        cursor_date=str(data.get("cursor_date", "")),
        last_completed_date=str(data.get("last_completed_date", "")),
        finalized_date=str(data.get("finalized_date", "")),
        status=str(data.get("status", "idle")),
        updated_at=str(data.get("updated_at", "")),
        last_error=str(data.get("last_error", "")),
    )


def save_collector_state(path: Path, state: CollectorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = _kst_now().isoformat()
    path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_collection_log(path: Path, record: CollectionLogRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(record), ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _manifest_path(base_dir: Path, dt: str) -> Path:
    return base_dir / f"{dt}.json"


def _manifest_index_path(base_dir: Path) -> Path:
    return base_dir / "index.json"


def _load_manifest(path: Path) -> Optional[CollectionDayManifest]:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt manifest JSON: %s", path)
        return None
    dt = str(payload.get("date", "")).strip()
    status = str(payload.get("status", "")).strip()
    if not dt or not status:
        return None
    return CollectionDayManifest(
        date=dt,
        status=status,
        status_reason=str(payload.get("status_reason", "")),
        has_partial_data=bool(payload.get("has_partial_data", status != "complete")),
        finalized_date=str(payload.get("finalized_date", "")),
        generated_at=str(payload.get("generated_at", "")),
        tickers=[str(ticker) for ticker in payload.get("tickers", [])],
        counts={str(key): int(value or 0) for key, value in dict(payload.get("counts", {})).items()},
        paths={str(key): str(value) for key, value in dict(payload.get("paths", {})).items()},
        news_range={str(key): str(value) for key, value in dict(payload.get("news_range", {})).items()},
        sources={str(key): str(value) for key, value in dict(payload.get("sources", {})).items()},
        exists={str(key): bool(value) for key, value in dict(payload.get("exists", {})).items()},
    )


def update_collection_manifest_index(base_dir: Path, manifest: CollectionDayManifest) -> None:
    path = _manifest_index_path(base_dir)
    entries_by_date: dict[str, CollectionManifestIndexEntry] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Corrupt index JSON, resetting: %s", path)
            payload = {}
        for row in payload.get("entries", []):
            dt = str(row.get("date", "")).strip()
            if not dt:
                continue
            entries_by_date[dt] = CollectionManifestIndexEntry(
                date=dt,
                status=str(row.get("status", "")),
                has_partial_data=bool(row.get("has_partial_data", False)),
                generated_at=str(row.get("generated_at", "")),
                manifest_path=str(row.get("manifest_path", "")),
            )

    manifest_path = _manifest_path(base_dir, manifest.date)
    entries_by_date[manifest.date] = CollectionManifestIndexEntry(
        date=manifest.date,
        status=manifest.status,
        has_partial_data=manifest.has_partial_data,
        generated_at=manifest.generated_at,
        manifest_path=str(manifest_path),
    )
    ordered_entries = [asdict(entries_by_date[dt]) for dt in sorted(entries_by_date.keys(), reverse=True)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": _kst_now().isoformat(),
                "entries": ordered_entries,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def write_collection_day_manifest(
    base_dir: Path,
    *,
    dt: str,
    status: str,
    status_reason: str,
    finalized_date: str,
    items: list[NewsDisclosure],
    tickers: list[str],
    news_count: int,
    classification_count: int,
    price_count: int,
    index_count: int,
    news_path: Path,
    classifications_path: Path,
    daily_prices_path: Path,
    daily_index_path: Path,
    daily_index_source: str = "pykrx",
) -> CollectionDayManifest:
    manifest = CollectionDayManifest(
        date=dt,
        status=status,
        status_reason=status_reason,
        has_partial_data=status != "complete",
        finalized_date=finalized_date,
        generated_at=_kst_now().isoformat(),
        tickers=tickers,
        counts={
            "news": news_count,
            "classifications": classification_count,
            "daily_prices": price_count,
            "daily_index": index_count,
        },
        paths={
            "news": str(news_path),
            "classifications": str(classifications_path),
            "daily_prices": str(daily_prices_path),
            "daily_index": str(daily_index_path),
        },
        news_range={
            "first_news_id": min((item.news_id for item in items), default=""),
            "last_news_id": max((item.news_id for item in items), default=""),
            "start_time": min((item.data_tm for item in items if item.data_tm), default=""),
            "end_time": max((item.data_tm for item in items if item.data_tm), default=""),
        },
        sources={
            "news": "collector",
            "classifications": "collector_classify",
            "daily_prices": "pykrx",
            "daily_index": daily_index_source,
        },
        exists={
            "news": news_path.exists(),
            "classifications": classifications_path.exists(),
            "daily_prices": daily_prices_path.exists(),
            "daily_index": daily_index_path.exists(),
        },
    )
    path = _manifest_path(base_dir, dt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_collection_manifest_index(base_dir, manifest)
    return manifest


def load_collection_log_summary(path: Path) -> CollectionLogSummary:
    latest_records: dict[str, CollectionLogRecord] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line in collection log")
                continue
            dt = str(row.get("date", "")).strip()
            status = str(row.get("status", "")).strip()
            if not dt or not status:
                continue
            latest_records[dt] = CollectionLogRecord(
                date=dt,
                status=status,
                news_count=int(row.get("news_count", 0) or 0),
                classification_count=int(row.get("classification_count", 0) or 0),
                daily_price_count=int(row.get("daily_price_count", 0) or 0),
                daily_index_count=int(row.get("daily_index_count", 0) or 0),
                completed_at=str(row.get("completed_at", "")),
                error=str(row.get("error", "")),
                skip_reason=str(row.get("skip_reason", "")),
            )

    ordered_dates = sorted(latest_records.keys(), reverse=True)
    latest_statuses: dict[str, str] = {}
    partial_dates: list[str] = []
    error_dates: list[str] = []
    for dt in ordered_dates:
        record = latest_records[dt]
        latest_statuses[dt] = "complete" if record.status == "skipped" else record.status
        if record.status == "partial":
            partial_dates.append(dt)
        elif record.status == "error":
            error_dates.append(dt)
    blocked_dates = partial_dates + error_dates
    status_generated_at = _kst_now().isoformat()
    status_generated_dt = _parse_iso_datetime(status_generated_at)
    oldest_blocked_age_seconds = 0
    if blocked_dates and status_generated_dt is not None:
        blocked_completed_ats = [
            parsed
            for dt in blocked_dates
            if (parsed := _parse_iso_datetime(latest_records[dt].completed_at)) is not None
        ]
        if blocked_completed_ats:
            oldest_blocked_age_seconds = max(
                0,
                int((status_generated_dt - min(blocked_completed_ats)).total_seconds()),
            )
    return CollectionLogSummary(
        latest_statuses=latest_statuses,
        latest_records=latest_records,
        partial_dates=partial_dates,
        error_dates=error_dates,
        tracked_dates=ordered_dates,
        oldest_partial_date=partial_dates[-1] if partial_dates else "",
        oldest_error_date=error_dates[-1] if error_dates else "",
        oldest_blocked_date=min(blocked_dates) if blocked_dates else "",
        blocked_news_count=sum(latest_records[dt].news_count for dt in blocked_dates),
        blocked_classification_count=sum(latest_records[dt].classification_count for dt in blocked_dates),
        blocked_price_count=sum(latest_records[dt].daily_price_count for dt in blocked_dates),
        blocked_index_count=sum(latest_records[dt].daily_index_count for dt in blocked_dates),
        status_generated_at=status_generated_at,
        oldest_blocked_age_seconds=oldest_blocked_age_seconds,
    )


def _load_latest_collection_statuses(path: Path) -> dict[str, str]:
    return load_collection_log_summary(path).latest_statuses


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.debug("Unparseable ISO datetime: %s", value)
        return None


def _record_to_dict(record: CollectionLogRecord) -> dict[str, Any]:
    return {
        "date": record.date,
        "status": record.status,
        "news_count": record.news_count,
        "classification_count": record.classification_count,
        "daily_price_count": record.daily_price_count,
        "daily_index_count": record.daily_index_count,
        "completed_at": record.completed_at,
        "error": record.error,
        "skip_reason": record.skip_reason,
    }


def _compute_status_health(state: CollectorState, summary: CollectionLogSummary) -> str:
    if state.status == "error":
        return "collector_error"
    if summary.error_dates:
        return "error_backlog"
    if summary.partial_dates:
        return "partial_backlog"
    return "healthy"


def _build_status_report(
    state: CollectorState,
    summary: CollectionLogSummary,
    *,
    backlog_limit: int,
) -> dict[str, Any]:
    limited_partial_dates = summary.partial_dates[:backlog_limit]
    limited_error_dates = summary.error_dates[:backlog_limit]
    health = _compute_status_health(state, summary)
    return {
        "state": {
            "status": state.status or "idle",
            "cursor_date": state.cursor_date,
            "finalized_date": state.finalized_date,
            "last_completed_date": state.last_completed_date,
            "updated_at": state.updated_at,
            "last_error": state.last_error,
        },
        "summary": {
            "health": health,
            "status_generated_at": summary.status_generated_at,
            "tracked_count": len(summary.tracked_dates),
            "partial_count": len(summary.partial_dates),
            "error_count": len(summary.error_dates),
            "oldest_partial_date": summary.oldest_partial_date,
            "oldest_error_date": summary.oldest_error_date,
            "oldest_blocked_date": summary.oldest_blocked_date,
            "oldest_blocked_age_seconds": summary.oldest_blocked_age_seconds,
            "blocked_news_count": summary.blocked_news_count,
            "blocked_classification_count": summary.blocked_classification_count,
            "blocked_price_count": summary.blocked_price_count,
            "blocked_index_count": summary.blocked_index_count,
        },
        "backlog": {
            "limit": backlog_limit,
            "partial_dates": limited_partial_dates,
            "error_dates": limited_error_dates,
            "partial_details": [_record_to_dict(summary.latest_records[dt]) for dt in limited_partial_dates],
            "error_details": [_record_to_dict(summary.latest_records[dt]) for dt in limited_error_dates],
        },
    }


def _parse_status_args(argv: list[str]) -> tuple[int, bool, str]:
    backlog_limit = 10
    as_json = False
    output_path = ""
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token == "--limit" and idx + 1 < len(argv):
            try:
                backlog_limit = max(0, int(argv[idx + 1]))
            except ValueError as exc:
                raise SystemExit("--limit must be an integer") from exc
            idx += 2
            continue
        if token == "--json":
            as_json = True
            idx += 1
            continue
        if token == "--output" and idx + 1 < len(argv):
            output_path = argv[idx + 1]
            idx += 2
            continue
        raise SystemExit("Usage: kindshot collect status [--limit N] [--json] [--output PATH]")
    if output_path and not as_json:
        raise SystemExit("--output requires --json")
    return backlog_limit, as_json, output_path


def log_collection_status(config: Config, *, backlog_limit: int = 10) -> CollectionLogSummary:
    state = load_collector_state(config.collector_state_path)
    summary = load_collection_log_summary(config.collector_log_path)
    health = _compute_status_health(state, summary)
    logger.info(
        "Collect status: health=%s state=%s cursor=%s finalized=%s last_completed=%s tracked=%d partial=%d error=%d oldest_partial=%s oldest_error=%s oldest_blocked=%s oldest_blocked_age_s=%d blocked_news=%d blocked_classified=%d blocked_prices=%d blocked_index=%d",
        health,
        state.status or "idle",
        state.cursor_date or "-",
        state.finalized_date or "-",
        state.last_completed_date or "-",
        len(summary.tracked_dates),
        len(summary.partial_dates),
        len(summary.error_dates),
        summary.oldest_partial_date or "-",
        summary.oldest_error_date or "-",
        summary.oldest_blocked_date or "-",
        summary.oldest_blocked_age_seconds,
        summary.blocked_news_count,
        summary.blocked_classification_count,
        summary.blocked_price_count,
        summary.blocked_index_count,
    )
    limited_partial_dates = summary.partial_dates[:backlog_limit]
    limited_error_dates = summary.error_dates[:backlog_limit]
    if limited_partial_dates:
        logger.info("Collect status partial backlog (showing %d): %s", len(limited_partial_dates), ",".join(limited_partial_dates))
        for dt in limited_partial_dates:
            record = summary.latest_records[dt]
            logger.info(
                "Collect status partial detail: date=%s reason=%s news=%d classified=%d prices=%d index=%d completed_at=%s",
                dt,
                record.skip_reason or "-",
                record.news_count,
                record.classification_count,
                record.daily_price_count,
                record.daily_index_count,
                record.completed_at or "-",
            )
    if limited_error_dates:
        logger.info("Collect status error backlog (showing %d): %s", len(limited_error_dates), ",".join(limited_error_dates))
        for dt in limited_error_dates:
            record = summary.latest_records[dt]
            logger.info(
                "Collect status error detail: date=%s error=%s news=%d classified=%d prices=%d index=%d completed_at=%s",
                dt,
                record.error or "-",
                record.news_count,
                record.classification_count,
                record.daily_price_count,
                record.daily_index_count,
                record.completed_at or "-",
            )
    return summary


def print_collection_status_json(config: Config, *, backlog_limit: int = 10, output_path: str = "") -> dict[str, Any]:
    state = load_collector_state(config.collector_state_path)
    summary = load_collection_log_summary(config.collector_log_path)
    report = _build_status_report(state, summary, backlog_limit=backlog_limit)
    payload = json.dumps(report, ensure_ascii=False)
    print(payload)
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
    return report


def _iter_backfill_dates(start: str, end: str) -> list[str]:
    current = _parse_yyyymmdd(start)
    stop = _parse_yyyymmdd(end)
    if current < stop:
        raise ValueError(f"start date must be >= end date: {start} < {end}")
    dates: list[str] = []
    while current >= stop:
        dates.append(_format_yyyymmdd(current))
        current -= timedelta(days=1)
    return dates


def _resolve_backfill_range(
    *,
    finalized_date: str,
    state_cursor_date: str,
    cursor: str = "",
    from_date: str = "",
    to_date: str = "",
) -> tuple[str, str]:
    if cursor and (from_date or to_date):
        raise ValueError("--cursor cannot be combined with --from/--to")

    if from_date and to_date:
        requested_start = max(from_date, to_date)
        requested_end = min(from_date, to_date)
    elif from_date:
        requested_start = finalized_date
        requested_end = from_date
    elif to_date:
        requested_start = state_cursor_date or finalized_date
        requested_end = to_date
    else:
        requested_start = cursor or state_cursor_date or finalized_date
        requested_end = finalized_date

    if _parse_yyyymmdd(requested_start) > _parse_yyyymmdd(finalized_date):
        requested_start = finalized_date
    if _parse_yyyymmdd(requested_end) > _parse_yyyymmdd(finalized_date):
        requested_end = finalized_date
    if _parse_yyyymmdd(requested_start) < _parse_yyyymmdd(requested_end):
        requested_start, requested_end = requested_end, requested_start
    return requested_start, requested_end


def _news_output_path(base_dir: Path, dt: str) -> Path:
    return base_dir / f"{dt}.jsonl"


def _jsonl_output_path(base_dir: Path, dt: str) -> Path:
    return base_dir / f"{dt}.jsonl"


def _join_status_reasons(*reasons: str) -> str:
    return ",".join(reason for reason in reasons if reason)


def _load_existing_news_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Skipping malformed news JSONL line")
            continue
        news_id = str(row.get("news_id", "")).strip()
        if news_id:
            ids.add(news_id)
    return ids


def _count_jsonl_records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)


def _news_record(item: NewsDisclosure, *, collected_at: str, source: str = "collector") -> dict[str, Any]:
    return {
        "news_id": item.news_id,
        "date": item.data_dt,
        "time": item.data_tm,
        "title": item.title,
        "dorg": item.dorg,
        "provider_code": item.provider_code,
        "tickers": list(item.tickers),
        "source": source,
        "collected_at": collected_at,
    }


def _classification_record(item: NewsDisclosure, *, classified_at: str) -> dict[str, Any]:
    result = classify(item.title)
    return {
        "news_id": item.news_id,
        "date": item.data_dt,
        "time": item.data_tm,
        "title": item.title,
        "bucket": result.bucket.value,
        "keyword_hits": result.keyword_hits,
        "tickers": list(item.tickers),
        "classified_at": classified_at,
    }


def append_news_items(base_dir: Path, dt: str, items: list[NewsDisclosure]) -> int:
    path = _news_output_path(base_dir, dt)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids = _load_existing_news_ids(path)
    collected_at = _kst_now().isoformat()
    new_rows = [
        json.dumps(_news_record(item, collected_at=collected_at), ensure_ascii=False)
        for item in items
        if item.news_id not in existing_ids
    ]
    if not new_rows:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for row in new_rows:
            f.write(row + "\n")
    return len(new_rows)


def append_classifications(base_dir: Path, dt: str, items: list[NewsDisclosure]) -> int:
    classified_at = _kst_now().isoformat()
    records = [
        _classification_record(item, classified_at=classified_at)
        for item in items
    ]
    return _append_records(base_dir, dt, records, key_field="news_id")


def _append_records(base_dir: Path, dt: str, records: list[dict[str, Any]], *, key_field: str) -> int:
    path = _jsonl_output_path(base_dir, dt)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_ids = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSONL line in %s", path)
                continue
            key = str(row.get(key_field, "")).strip()
            if key:
                existing_ids.add(key)
    new_rows = [
        json.dumps(record, ensure_ascii=False)
        for record in records
        if str(record.get(key_field, "")).strip() not in existing_ids
    ]
    if not new_rows:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for row in new_rows:
            f.write(row + "\n")
    return len(new_rows)


async def _collect_daily_prices(dt: str, tickers: list[str]) -> list[dict[str, Any]]:
    if not tickers:
        return []

    def _fetch() -> list[dict[str, Any]]:
        try:
            from pykrx import stock
        except ImportError:
            logger.warning("pykrx unavailable, skipping daily price collection for %s", dt)
            return []

        collected_at = _kst_now().isoformat()
        rows: list[dict[str, Any]] = []
        for ticker in tickers:
            try:
                frame = stock.get_market_ohlcv_by_date(dt, dt, ticker)
                cap_frame = stock.get_market_cap_by_date(dt, dt, ticker)
            except Exception:
                logger.exception("pykrx daily price fetch failed for %s on %s", ticker, dt)
                continue
            if frame.empty:
                continue
            ohlcv = frame.iloc[0]
            cap_row = cap_frame.iloc[0] if not cap_frame.empty else None
            rows.append(
                {
                    "ticker_date": f"{ticker}:{dt}",
                    "ticker": ticker,
                    "date": dt,
                    "open": float(ohlcv["시가"]),
                    "high": float(ohlcv["고가"]),
                    "low": float(ohlcv["저가"]),
                    "close": float(ohlcv["종가"]),
                    "volume": int(ohlcv["거래량"]),
                    "value": float(ohlcv["거래대금"]) if "거래대금" in frame.columns else None,
                    "market_cap": float(cap_row["시가총액"]) if cap_row is not None and "시가총액" in cap_frame.columns else None,
                    "collected_at": collected_at,
                }
            )
        return rows

    return await asyncio.to_thread(_fetch)


async def _collect_daily_index(kis: KisClient, dt: str) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    collected_at = _kst_now().isoformat()

    # KIS provides a first-party domestic index daily endpoint; keep pykrx as fallback only.
    for kis_iscd, stored_code in (("0001", "1001"), ("2001", "2001")):
        info = await kis.get_index_daily_info(kis_iscd, dt)
        if info is None:
            continue
        rows.append(
            {
                "index_date": f"{stored_code}:{dt}",
                "index_code": stored_code,
                "date": dt,
                "open": info.open_px,
                "high": info.high,
                "low": info.low,
                "close": info.close,
                "volume": info.volume,
                "value": info.value,
                "collected_at": collected_at,
            }
        )
    if rows:
        return rows, "kis"

    def _fetch() -> list[dict[str, Any]]:
        try:
            from pykrx import stock
        except ImportError:
            logger.warning("pykrx unavailable, skipping daily index collection for %s", dt)
            return [], "pykrx"

        rows: list[dict[str, Any]] = []
        for index_code in ("1001", "2001"):
            try:
                frame = stock.get_index_ohlcv_by_date(dt, dt, index_code, name_display=False)
            except Exception:
                logger.exception("pykrx daily index fetch failed for %s on %s", index_code, dt)
                continue
            if frame.empty:
                continue
            row = frame.iloc[0]
            rows.append(
                {
                    "index_date": f"{index_code}:{dt}",
                    "index_code": index_code,
                    "date": dt,
                    "open": float(row["시가"]),
                    "high": float(row["고가"]),
                    "low": float(row["저가"]),
                    "close": float(row["종가"]),
                    "volume": int(row["거래량"]) if "거래량" in frame.columns else None,
                    "value": float(row["거래대금"]) if "거래대금" in frame.columns else None,
                    "collected_at": collected_at,
                }
            )
        return rows, "pykrx"

    return await asyncio.to_thread(_fetch)


async def _is_market_business_day(dt: str) -> bool:
    """Return True when a representative KRX equity has daily OHLCV for the date."""

    def _fetch() -> bool:
        try:
            from pykrx import stock
        except ImportError:
            logger.warning("pykrx unavailable, cannot verify business day for %s", dt)
            return True

        for ticker in ("005930", "000660"):
            try:
                frame = stock.get_market_ohlcv_by_date(dt, dt, ticker)
            except Exception:
                logger.exception("pykrx business-day probe failed for %s on %s", ticker, dt)
                return True
            if not frame.empty:
                return True
        return False

    return await asyncio.to_thread(_fetch)


async def _is_trusted_complete_date(config: Config, dt: str) -> bool:
    manifest = _load_manifest(_manifest_path(config.collector_manifests_dir, dt))
    if manifest is None or manifest.status != "complete":
        return False
    if not await _is_market_business_day(dt):
        return False
    if manifest.counts.get("daily_index", 0) <= 0:
        return False
    if manifest.tickers and manifest.counts.get("daily_prices", 0) <= 0:
        return False
    return True


async def _collect_news_for_date(kis: KisClient, dt: str) -> list[NewsDisclosure]:
    return await kis.get_news_disclosure_items(date=f"00{dt}", from_time="235959")


async def _collect_news_fetch_result_for_date(kis: KisClient, dt: str) -> NewsDisclosureFetchResult:
    return await kis.get_news_disclosure_fetch_result(date=f"00{dt}", from_time="235959")


async def _collect_news_for_date_with_retry(
    kis: KisClient,
    dt: str,
    *,
    max_attempts: int,
    retry_delay_s: float,
) -> NewsDisclosureFetchResult:
    attempts = max(1, max_attempts)
    delay_s = max(0.0, retry_delay_s)
    for attempt in range(1, attempts + 1):
        try:
            return await _collect_news_fetch_result_for_date(kis, dt)
        except Exception:
            if attempt >= attempts:
                logger.exception("Collect backfill: news fetch failed for %s after %d attempts", dt, attempt)
                raise
            logger.warning(
                "Collect backfill: news fetch failed for %s on attempt %d/%d; retrying in %.1fs",
                dt,
                attempt,
                attempts,
                delay_s * attempt,
            )
            await asyncio.sleep(delay_s * attempt)
    return NewsDisclosureFetchResult(items=[])


async def _collect_all_news_for_date(
    kis: KisClient,
    dt: str,
    *,
    max_attempts: int,
    retry_delay_s: float,
) -> NewsDisclosureFetchResult:
    from_time = "235959"
    aggregated: list[NewsDisclosure] = []
    seen_ids: set[str] = set()
    pagination_truncated = False

    while True:
        fetch_result = await _collect_news_for_date_with_retry(
            kis,
            dt,
            max_attempts=max_attempts,
            retry_delay_s=retry_delay_s,
        ) if from_time == "235959" else await _collect_news_fetch_result_for_date_with_retry_window(
            kis,
            dt,
            from_time=from_time,
            max_attempts=max_attempts,
            retry_delay_s=retry_delay_s,
        )

        for item in fetch_result.items:
            if item.news_id in seen_ids:
                continue
            seen_ids.add(item.news_id)
            aggregated.append(item)

        if not fetch_result.pagination_truncated:
            return NewsDisclosureFetchResult(items=aggregated, pagination_truncated=pagination_truncated)

        min_time = min((item.data_tm for item in fetch_result.items if len(item.data_tm) == 6 and item.data_tm.isdigit()), default="")
        next_from_time = _decrement_hhmmss(min_time) if min_time else ""
        if not next_from_time or next_from_time >= from_time:
            pagination_truncated = True
            return NewsDisclosureFetchResult(items=aggregated, pagination_truncated=True)
        from_time = next_from_time


async def _collect_news_fetch_result_for_date_with_retry_window(
    kis: KisClient,
    dt: str,
    *,
    from_time: str,
    max_attempts: int,
    retry_delay_s: float,
) -> NewsDisclosureFetchResult:
    attempts = max(1, max_attempts)
    delay_s = max(0.0, retry_delay_s)
    for attempt in range(1, attempts + 1):
        try:
            return await kis.get_news_disclosure_fetch_result(date=f"00{dt}", from_time=from_time)
        except Exception:
            if attempt >= attempts:
                logger.exception("Collect backfill: news fetch failed for %s from_time=%s after %d attempts", dt, from_time, attempt)
                raise
            logger.warning(
                "Collect backfill: news fetch failed for %s from_time=%s on attempt %d/%d; retrying in %.1fs",
                dt,
                from_time,
                attempt,
                attempts,
                delay_s * attempt,
            )
            await asyncio.sleep(delay_s * attempt)
    return NewsDisclosureFetchResult(items=[])


async def run_backfill(
    config: Config,
    *,
    cursor: str = "",
    from_date: str = "",
    to_date: str = "",
) -> BackfillResult:
    finalized_date = compute_finalized_date(
        cutoff_hour=config.finalize_cutoff_hour_kst,
        cutoff_minute=config.finalize_cutoff_minute_kst,
    )
    state = load_collector_state(config.collector_state_path)
    state.finalized_date = finalized_date

    requested_start, requested_end = _resolve_backfill_range(
        finalized_date=finalized_date,
        state_cursor_date=state.cursor_date,
        cursor=cursor,
        from_date=from_date,
        to_date=to_date,
    )

    dates = _iter_backfill_dates(requested_start, requested_end)
    summary = load_collection_log_summary(config.collector_log_path)
    latest_records = summary.latest_records
    processed_dates: list[str] = []
    completed_dates: list[str] = []
    partial_dates: list[str] = []
    skipped_dates: list[str] = []
    news_counts: dict[str, int] = {}
    classification_counts: dict[str, int] = {}
    price_counts: dict[str, int] = {}
    index_counts: dict[str, int] = {}

    async with aiohttp.ClientSession() as session:
        kis = KisClient(config, session)
        state.status = "running"
        state.cursor_date = requested_start
        save_collector_state(config.collector_state_path, state)

        try:
            for dt in dates:
                latest_record = latest_records.get(dt)
                if latest_record is not None and latest_record.status in {"complete", "skipped"}:
                    if await _is_trusted_complete_date(config, dt):
                        logger.info("Collect backfill: skipping already completed date %s", dt)
                        append_collection_log(
                            config.collector_log_path,
                            CollectionLogRecord(
                                date=dt,
                                status="skipped",
                                news_count=0,
                                classification_count=0,
                                daily_price_count=0,
                                daily_index_count=0,
                                completed_at=_kst_now().isoformat(),
                                skip_reason="already_complete",
                            ),
                        )
                        skipped_dates.append(dt)
                        next_date = _parse_yyyymmdd(dt) - timedelta(days=1)
                        state.cursor_date = _format_yyyymmdd(next_date)
                        save_collector_state(config.collector_state_path, state)
                        continue
                    logger.info("Collect backfill: reprocessing stale complete date %s", dt)
                    if state.last_completed_date == dt:
                        state.last_completed_date = ""
                if not await _is_market_business_day(dt):
                    logger.info("Collect backfill: skipping non-trading day %s", dt)
                    if state.last_completed_date == dt:
                        state.last_completed_date = ""
                    append_collection_log(
                        config.collector_log_path,
                        CollectionLogRecord(
                            date=dt,
                            status="skipped",
                            news_count=0,
                            classification_count=0,
                            daily_price_count=0,
                            daily_index_count=0,
                            completed_at=_kst_now().isoformat(),
                            skip_reason="non_trading_day",
                        ),
                    )
                    skipped_dates.append(dt)
                    next_date = _parse_yyyymmdd(dt) - timedelta(days=1)
                    state.cursor_date = _format_yyyymmdd(next_date)
                    save_collector_state(config.collector_state_path, state)
                    continue
                logger.info("Collect backfill: fetching news for %s", dt)
                fetch_result = await _collect_all_news_for_date(
                    kis,
                    dt,
                    max_attempts=config.collector_news_max_attempts,
                    retry_delay_s=config.collector_retry_delay_s,
                )
                items = fetch_result.items
                append_news_items(config.collector_news_dir, dt, items)
                news_counts[dt] = _count_jsonl_records(_news_output_path(config.collector_news_dir, dt))
                append_classifications(config.collector_classifications_dir, dt, items)
                classification_counts[dt] = _count_jsonl_records(
                    _jsonl_output_path(config.collector_classifications_dir, dt)
                )

                tickers = sorted({ticker for item in items for ticker in item.tickers})
                has_replayable_inputs = bool(tickers) or news_counts[dt] > 0 or classification_counts[dt] > 0
                daily_prices = await _collect_daily_prices(dt, tickers)
                _append_records(
                    config.collector_daily_prices_dir,
                    dt,
                    daily_prices,
                    key_field="ticker_date",
                )
                price_counts[dt] = _count_jsonl_records(_jsonl_output_path(config.collector_daily_prices_dir, dt))

                daily_index, daily_index_source = await _collect_daily_index(kis, dt)
                _append_records(
                    config.collector_index_dir,
                    dt,
                    daily_index,
                    key_field="index_date",
                )
                index_counts[dt] = _count_jsonl_records(_jsonl_output_path(config.collector_index_dir, dt))
                status_reason = _join_status_reasons(
                    "pagination_truncated" if fetch_result.pagination_truncated else "",
                    "daily_prices_missing" if tickers and price_counts[dt] == 0 else "",
                    "daily_index_missing" if has_replayable_inputs and index_counts[dt] == 0 else "",
                )
                status = "partial" if status_reason else "complete"
                write_collection_day_manifest(
                    config.collector_manifests_dir,
                    dt=dt,
                    status=status,
                    status_reason=status_reason,
                    finalized_date=finalized_date,
                    items=items,
                    tickers=tickers,
                    news_count=news_counts[dt],
                    classification_count=classification_counts[dt],
                    price_count=price_counts[dt],
                    index_count=index_counts[dt],
                    news_path=_news_output_path(config.collector_news_dir, dt),
                    classifications_path=_jsonl_output_path(config.collector_classifications_dir, dt),
                    daily_prices_path=_jsonl_output_path(config.collector_daily_prices_dir, dt),
                    daily_index_path=_jsonl_output_path(config.collector_index_dir, dt),
                    daily_index_source=daily_index_source,
                )
                append_collection_log(
                    config.collector_log_path,
                    CollectionLogRecord(
                        date=dt,
                        status=status,
                        news_count=news_counts[dt],
                        classification_count=classification_counts[dt],
                        daily_price_count=price_counts[dt],
                        daily_index_count=index_counts[dt],
                        completed_at=_kst_now().isoformat(),
                        skip_reason=status_reason,
                    ),
                )
                processed_dates.append(dt)
                if status == "complete":
                    completed_dates.append(dt)
                    state.last_completed_date = dt
                    next_date = _parse_yyyymmdd(dt) - timedelta(days=1)
                    state.cursor_date = _format_yyyymmdd(next_date)
                else:
                    if state.last_completed_date == dt:
                        state.last_completed_date = ""
                    partial_dates.append(dt)
                    state.cursor_date = dt
                save_collector_state(config.collector_state_path, state)
        except Exception as exc:
            failed_date = state.cursor_date or requested_start
            append_collection_log(
                config.collector_log_path,
                CollectionLogRecord(
                    date=failed_date,
                    status="error",
                    news_count=news_counts.get(failed_date, 0),
                    classification_count=classification_counts.get(failed_date, 0),
                    daily_price_count=price_counts.get(failed_date, 0),
                    daily_index_count=index_counts.get(failed_date, 0),
                    completed_at=_kst_now().isoformat(),
                    error=str(exc),
                ),
            )
            state.status = "error"
            state.last_error = str(exc)
            save_collector_state(config.collector_state_path, state)
            raise
        else:
            state.status = "idle"
            state.last_error = ""
            save_collector_state(config.collector_state_path, state)

    return BackfillResult(
        requested_from=requested_start,
        requested_to=requested_end,
        finalized_date=finalized_date,
        processed_dates=processed_dates,
        completed_dates=completed_dates,
        partial_dates=partial_dates,
        news_counts=news_counts,
        classification_counts=classification_counts,
        price_counts=price_counts,
        index_counts=index_counts,
        skipped_dates=skipped_dates,
    )


def _parse_collect_args(argv: list[str]) -> tuple[str, str, str]:
    cursor = ""
    from_date = ""
    to_date = ""
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token == "--cursor" and idx + 1 < len(argv):
            cursor = argv[idx + 1]
            idx += 2
            continue
        if token == "--from" and idx + 1 < len(argv):
            from_date = argv[idx + 1]
            idx += 2
            continue
        if token == "--to" and idx + 1 < len(argv):
            to_date = argv[idx + 1]
            idx += 2
            continue
        raise SystemExit(f"Unknown collect backfill argument: {token}")
    return cursor, from_date, to_date


async def collect_main(argv: list[str], config: Config) -> None:
    if not argv:
        raise SystemExit("Usage: kindshot collect <backfill|status> [collect options]")
    task = argv[0]
    if task == "status":
        backlog_limit, as_json, output_path = _parse_status_args(argv[1:])
        if as_json:
            print_collection_status_json(config, backlog_limit=backlog_limit, output_path=output_path)
        else:
            log_collection_status(config, backlog_limit=backlog_limit)
        return
    if task == "backfill":
        cursor, from_date, to_date = _parse_collect_args(argv[1:])
        result = await run_backfill(config, cursor=cursor, from_date=from_date, to_date=to_date)
        logger.info(
            "Collect backfill completed: from=%s to=%s finalized=%s processed=%d complete=%d partial=%d skipped=%d",
            result.requested_from,
            result.requested_to,
            result.finalized_date,
            len(result.processed_dates),
            len(result.completed_dates),
            len(result.partial_dates),
            len(result.skipped_dates),
        )
        return
    raise SystemExit(f"Unknown collect task: {task}")
