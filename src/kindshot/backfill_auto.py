"""Helpers for scheduler-friendly collector backfill automation."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterator, Optional

from kindshot.collector import (
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
