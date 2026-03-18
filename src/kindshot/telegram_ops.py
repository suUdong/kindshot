"""Telegram helpers for operator-facing notifications."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from kindshot.collector import BackfillResult, CollectionLogSummary, CollectorState


def _format_reason_pairs(dates: list[str], summary: CollectionLogSummary) -> str:
    parts: list[str] = []
    for dt in dates[:5]:
        record = summary.latest_records.get(dt)
        if record is None or not record.skip_reason:
            continue
        parts.append(f"{dt}:{record.skip_reason}")
    return ";".join(parts)


def send_telegram_message(text: str, bot_token: str, chat_id: str, *, timeout_s: float = 10.0) -> bool:
    """Send a Telegram Bot API message using only stdlib."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout_s) as resp:
        result = json.loads(resp.read())
    return bool(result.get("ok"))


def format_backfill_notification(
    result: BackfillResult | None,
    state: CollectorState,
    summary: CollectionLogSummary,
    *,
    error: Exception | None = None,
) -> str:
    """Format a concise Telegram-safe backfill notification."""
    status = "FAIL" if error is not None else "OK"
    lines = [f"Kindshot Backfill {status}"]

    if result is not None:
        lines.append(
            "range="
            f"{result.requested_from}->{result.requested_to} finalized={result.finalized_date}"
        )
        lines.append(
            "processed="
            f"{len(result.processed_dates)} complete={len(result.completed_dates)} "
            f"partial={len(result.partial_dates)} skipped={len(result.skipped_dates)}"
        )
        if result.partial_dates:
            lines.append(f"partial_dates={','.join(result.partial_dates[:5])}")
            if partial_reasons := _format_reason_pairs(result.partial_dates, summary):
                lines.append(f"partial_reasons={partial_reasons}")
        if result.skipped_dates:
            lines.append(f"skipped_dates={','.join(result.skipped_dates[:5])}")
            if skip_reasons := _format_reason_pairs(result.skipped_dates, summary):
                lines.append(f"skip_reasons={skip_reasons}")

    lines.append(
        "collector="
        f"{state.status or 'idle'} cursor={state.cursor_date or '-'} "
        f"last_completed={state.last_completed_date or '-'}"
    )
    lines.append(
        "backlog="
        f"partial={len(summary.partial_dates)} error={len(summary.error_dates)} "
        f"oldest_blocked={summary.oldest_blocked_date or '-'}"
    )

    if error is not None:
        lines.append(f"error={type(error).__name__}: {error}")

    return "\n".join(lines)
