"""Helpers for scheduler-friendly collector backfill automation."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from kindshot.collector import (
    BackfillResult,
    CollectorState,
    _format_yyyymmdd,
    _parse_yyyymmdd,
    compute_finalized_date,
    load_collection_log_summary,
    load_collector_state,
)
from kindshot.config import Config


@dataclass(frozen=True)
class AutoBackfillPlan:
    finalized_date: str
    requested_from: str
    requested_to: str
    max_days: int
    cursor_date: str
    oldest_date: str


def default_lock_path(config: Config) -> Path:
    return config.data_dir / "collector" / "backfill_auto.lock"


def _auto_report_output_path(config: Config, explicit_path: str = "") -> Path:
    if explicit_path:
        return Path(explicit_path)
    return config.collector_backfill_auto_report_path


def compute_auto_backfill_plan(
    config: Config,
    *,
    max_days: int,
    oldest_date: str = "",
) -> Optional[AutoBackfillPlan]:
    if max_days <= 0:
        raise ValueError("max_days must be >= 1")

    finalized_date = compute_finalized_date(
        cutoff_hour=config.finalize_cutoff_hour_kst,
        cutoff_minute=config.finalize_cutoff_minute_kst,
    )
    state = load_collector_state(config.collector_state_path)
    summary = load_collection_log_summary(config.collector_log_path)
    blocked_start = summary.partial_dates[0] if summary.partial_dates else (summary.error_dates[0] if summary.error_dates else "")
    cursor_date = state.cursor_date or finalized_date
    start = min(blocked_start or cursor_date, finalized_date)

    if oldest_date and _parse_yyyymmdd(start) < _parse_yyyymmdd(oldest_date):
        return None

    end_date = _parse_yyyymmdd(start) - timedelta(days=max_days - 1)
    end = _format_yyyymmdd(end_date)
    if oldest_date and _parse_yyyymmdd(end) < _parse_yyyymmdd(oldest_date):
        end = oldest_date

    return AutoBackfillPlan(
        finalized_date=finalized_date,
        requested_from=start,
        requested_to=end,
        max_days=max_days,
        cursor_date=state.cursor_date,
        oldest_date=oldest_date,
    )


def format_auto_noop_message(plan: AutoBackfillPlan | None, *, cursor_date: str, oldest_date: str, finalized_date: str) -> str:
    return "\n".join(
        [
            "Kindshot Backfill AUTO NOOP",
            f"finalized={finalized_date}",
            f"cursor={cursor_date or '-'} oldest={oldest_date or '-'}",
            "reason=backfill_floor_reached",
            f"planned={'-' if plan is None else f'{plan.requested_from}->{plan.requested_to}'}",
        ]
    )


def build_auto_backfill_round_report(round_number: int, plan: AutoBackfillPlan, result: BackfillResult) -> dict[str, Any]:
    return {
        "round": round_number,
        "requested_from": plan.requested_from,
        "requested_to": plan.requested_to,
        "finalized_date": result.finalized_date,
        "processed_dates": list(result.processed_dates),
        "completed_dates": list(result.completed_dates),
        "partial_dates": list(result.partial_dates),
        "skipped_dates": list(result.skipped_dates),
        "news_counts": dict(result.news_counts),
        "classification_counts": dict(result.classification_counts),
        "price_counts": dict(result.price_counts),
        "index_counts": dict(result.index_counts),
    }


def build_auto_backfill_report(
    *,
    max_days: int,
    max_rounds: int,
    stop_hour: int,
    oldest_date: str,
    notify_noop: bool,
    stop_reason: str,
    rounds: list[dict[str, Any]],
    state: CollectorState,
    status_report: dict[str, Any],
    latest_backfill_report_path: str = "",
    error: Exception | None = None,
) -> dict[str, Any]:
    total_processed_dates = sum(len(row.get("processed_dates", [])) for row in rounds)
    total_completed_dates = sum(len(row.get("completed_dates", [])) for row in rounds)
    total_partial_dates = sum(len(row.get("partial_dates", [])) for row in rounds)
    total_skipped_dates = sum(len(row.get("skipped_dates", [])) for row in rounds)
    status = "success"
    if error is not None:
        status = "error"
    elif not rounds and stop_reason == "backfill_floor_reached":
        status = "noop"
    elif stop_reason in {"stop_hour_reached", "max_rounds_reached"}:
        status = "stopped"
    return {
        "source": "collect_backfill_auto",
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "request": {
            "max_days": max_days,
            "max_rounds": max_rounds,
            "stop_hour_kst": stop_hour,
            "oldest_date": oldest_date,
            "notify_noop": notify_noop,
        },
        "result": {
            "status": status,
            "stop_reason": stop_reason,
            "round_count": len(rounds),
            "total_processed_dates": total_processed_dates,
            "total_completed_dates": total_completed_dates,
            "total_partial_dates": total_partial_dates,
            "total_skipped_dates": total_skipped_dates,
            "latest_backfill_report_path": latest_backfill_report_path,
        },
        "rounds": rounds,
        "collector_state": asdict(state),
        "collector_status": status_report,
        "error": (
            {
                "type": type(error).__name__,
                "message": str(error),
            }
            if error is not None
            else None
        ),
    }


def write_auto_backfill_report(
    config: Config,
    *,
    max_days: int,
    max_rounds: int,
    stop_hour: int,
    oldest_date: str,
    notify_noop: bool,
    stop_reason: str,
    rounds: list[dict[str, Any]],
    state: CollectorState,
    status_report: dict[str, Any],
    latest_backfill_report_path: str = "",
    error: Exception | None = None,
    output_path: str = "",
) -> tuple[dict[str, Any], Path]:
    report = build_auto_backfill_report(
        max_days=max_days,
        max_rounds=max_rounds,
        stop_hour=stop_hour,
        oldest_date=oldest_date,
        notify_noop=notify_noop,
        stop_reason=stop_reason,
        rounds=rounds,
        state=state,
        status_report=status_report,
        latest_backfill_report_path=latest_backfill_report_path,
        error=error,
    )
    path = _auto_report_output_path(config, output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, path


@contextmanager
def backfill_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\n")
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass  # Lock already removed — expected race
