"""Telegram helpers for operator-facing notifications."""

from __future__ import annotations

import json
import logging
import os
from urllib.request import Request, urlopen

from kindshot.collector import BackfillResult, CollectionLogSummary, CollectorState

logger = logging.getLogger(__name__)


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


def format_buy_signal(
    *,
    ticker: str,
    corp_name: str,
    headline: str,
    bucket: str,
    confidence: int,
    size_hint: str,
    reason: str,
    keyword_hits: list[str] | None = None,
    hold_minutes: int = 0,
    ret_today: float | None = None,
    spread_bps: float | None = None,
    adv_display: str = "",
    mode: str = "paper",
    decision_source: str = "LLM",
) -> str:
    """Format a real-time BUY signal notification for Telegram."""
    hold_label = "EOD" if hold_minutes == 0 else f"{hold_minutes}m"
    source_tag = f" [{decision_source}]" if decision_source != "LLM" else ""
    lines = [
        f"{'🟢' if mode == 'live' else '📋'} [{mode.upper()}] BUY {corp_name}({ticker}){source_tag}",
        f"conf={confidence} size={size_hint} hold={hold_label}",
    ]
    # BUY 이유를 가장 눈에 띄게 표시
    if reason:
        lines.append(f">> {reason}")
    # 시장 컨텍스트
    ctx_parts = []
    if ret_today is not None:
        ctx_parts.append(f"ret={ret_today:+.1f}%")
    if spread_bps is not None:
        ctx_parts.append(f"spread={spread_bps:.0f}bp")
    if adv_display:
        ctx_parts.append(f"adv={adv_display}")
    if ctx_parts:
        lines.append(" ".join(ctx_parts))
    if keyword_hits:
        lines.append(f"kw: {', '.join(keyword_hits[:5])}")
    lines.append(headline[:120])
    return "\n".join(lines)


def format_high_conf_skip_signal(
    *,
    ticker: str,
    corp_name: str,
    headline: str,
    confidence: int,
    skip_reason: str,
    decision_source: str = "LLM",
    mode: str = "paper",
) -> str:
    """Format a high-confidence SKIP notification for monitoring false negatives."""
    source_tag = f" [{decision_source}]" if decision_source != "LLM" else ""
    lines = [
        f"⚠️ [{mode.upper()}] HIGH-CONF SKIP {corp_name}({ticker}){source_tag}",
        f"conf={confidence} blocked={skip_reason}",
        headline[:120],
    ]
    return "\n".join(lines)


def try_send_high_conf_skip(
    *,
    ticker: str,
    corp_name: str,
    headline: str,
    confidence: int,
    skip_reason: str,
    decision_source: str = "LLM",
    mode: str = "paper",
) -> bool:
    """Best-effort high-confidence SKIP telegram notification. Never raises."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return False
    try:
        text = format_high_conf_skip_signal(
            ticker=ticker, corp_name=corp_name, headline=headline,
            confidence=confidence, skip_reason=skip_reason,
            decision_source=decision_source, mode=mode,
        )
        return send_telegram_message(text, bot_token, chat_id)
    except Exception:
        logger.debug("High-conf SKIP telegram send failed", exc_info=True)
        return False


def try_send_buy_signal(
    *,
    ticker: str,
    corp_name: str,
    headline: str,
    bucket: str,
    confidence: int,
    size_hint: str,
    reason: str,
    keyword_hits: list[str] | None = None,
    hold_minutes: int = 0,
    ret_today: float | None = None,
    spread_bps: float | None = None,
    adv_display: str = "",
    mode: str = "paper",
    decision_source: str = "LLM",
) -> bool:
    """Best-effort BUY signal telegram notification. Never raises."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return False
    try:
        text = format_buy_signal(
            ticker=ticker, corp_name=corp_name, headline=headline,
            bucket=bucket, confidence=confidence, size_hint=size_hint,
            reason=reason, keyword_hits=keyword_hits,
            hold_minutes=hold_minutes, ret_today=ret_today,
            spread_bps=spread_bps, adv_display=adv_display, mode=mode,
            decision_source=decision_source,
        )
        return send_telegram_message(text, bot_token, chat_id)
    except Exception:
        logger.debug("BUY signal telegram send failed", exc_info=True)
        return False
