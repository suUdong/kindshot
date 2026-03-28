"""Telegram helpers for operator-facing notifications."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from kindshot.collector import BackfillResult, CollectionLogSummary, CollectorState
from kindshot.performance import DailySummary
from kindshot.tz import KST as _KST

logger = logging.getLogger(__name__)


def _format_reason_pairs(dates: list[str], summary: CollectionLogSummary) -> str:
    parts: list[str] = []
    for dt in dates[:5]:
        record = summary.latest_records.get(dt)
        if record is None or not record.skip_reason:
            continue
        parts.append(f"{dt}:{record.skip_reason}")
    return ";".join(parts)


def _detail_rows_by_date(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("date", "")).strip(): row
        for row in rows
        if str(row.get("date", "")).strip()
    }


def _sanitize_path(path_str: str) -> str:
    """서버 절대 경로 노출 방지: 알려진 프로젝트 하위 디렉토리 기준으로 상대 경로만 반환."""
    if not path_str:
        return path_str
    for marker in ("data/", "logs/"):
        idx = path_str.find(marker)
        if idx >= 0:
            return path_str[idx:]
    # 알려진 마커가 없으면 파일명만 반환
    from pathlib import PurePosixPath
    return PurePosixPath(path_str).name


def _report_path_lines(report_paths: dict[str, str] | None) -> list[str]:
    if not report_paths:
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for label in ("backfill_report", "auto_report"):
        path = _sanitize_path(str(report_paths.get(label, "") or "").strip())
        if path:
            lines.append(f"{label}={path}")
            seen.add(label)
    for label, raw_path in report_paths.items():
        if label in seen:
            continue
        path = _sanitize_path(str(raw_path or "").strip())
        if path:
            lines.append(f"{label}={path}")
    return lines


def _format_blocked_detail_line(label: str, detail: dict[str, Any], *, primary_key: str) -> str:
    primary_value = str(detail.get(primary_key, "") or "").strip()
    manifest_reason = str(detail.get("manifest_status_reason", "") or "").strip()
    manifest_status = str(detail.get("manifest_status", "") or "").strip()
    manifest_path = str(detail.get("manifest_path", "") or "").strip()
    parts = [f"{label}={detail.get('date', '-') or '-'}"]
    if primary_value:
        key_name = "error" if primary_key == "error" else "reason"
        parts.append(f"{key_name}={primary_value}")
    if manifest_reason and manifest_reason != primary_value:
        parts.append(f"manifest_reason={manifest_reason}")
    if manifest_status:
        parts.append(f"manifest_status={manifest_status}")
    if manifest_path:
        parts.append(f"manifest={_sanitize_path(manifest_path)}")
    return " ".join(parts)


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


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() and os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def _telegram_target() -> tuple[str, str] | None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return None
    return bot_token, chat_id


def format_backfill_notification(
    result: BackfillResult | None,
    state: CollectorState,
    summary: CollectionLogSummary,
    *,
    error: Exception | None = None,
    status_report: dict[str, Any] | None = None,
    report_paths: dict[str, str] | None = None,
) -> str:
    """Format a concise Telegram-safe backfill notification."""
    status = "FAIL" if error is not None else "OK"
    lines = [f"Kindshot Backfill {status}"]
    summary_payload = dict(status_report.get("summary", {})) if status_report is not None else {}
    backlog_payload = dict(status_report.get("backlog", {})) if status_report is not None else {}
    partial_detail_map = _detail_rows_by_date(list(backlog_payload.get("partial_details", [])))
    error_details = list(backlog_payload.get("error_details", []))

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
            for dt in result.partial_dates[:3]:
                detail = partial_detail_map.get(dt)
                if detail is not None:
                    lines.append(_format_blocked_detail_line("partial_detail", detail, primary_key="skip_reason"))
        if result.skipped_dates:
            lines.append(f"skipped_dates={','.join(result.skipped_dates[:5])}")
            if skip_reasons := _format_reason_pairs(result.skipped_dates, summary):
                lines.append(f"skip_reasons={skip_reasons}")

    lines.extend(_report_path_lines(report_paths))
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
    if summary_payload:
        lines.append(
            "backlog_health="
            f"{summary_payload.get('health', '-') or '-'} "
            f"oldest_blocked_age_s={summary_payload.get('oldest_blocked_age_seconds', 0)}"
        )
    for detail in error_details[:3]:
        lines.append(_format_blocked_detail_line("error_detail", detail, primary_key="error"))

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
    tp_pct: float | None = None,
    sl_pct: float | None = None,
) -> str:
    """Format a real-time BUY signal notification for Telegram."""
    hold_label = "EOD" if hold_minutes == 0 else f"{hold_minutes}m"
    source_tag = f" [{decision_source}]" if decision_source != "LLM" else ""
    lines = [
        f"{'🟢' if mode == 'live' else '📋'} [{mode.upper()}] BUY {corp_name}({ticker}){source_tag}",
        f"conf={confidence} size={size_hint} hold={hold_label}",
    ]
    # TP/SL 타겟 표시
    if tp_pct is not None and sl_pct is not None:
        lines.append(f"TP={tp_pct:+.1f}% SL={sl_pct:.1f}%")
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
    shadow_scheduled: bool = False,
    decision_source: str = "LLM",
    mode: str = "paper",
) -> str:
    """Format a guardrail block notification for monitoring false negatives."""
    source_tag = f" [{decision_source}]" if decision_source != "LLM" else ""
    shadow_tag = "scheduled" if shadow_scheduled else "not_scheduled"
    lines = [
        f"⚠️ [{mode.upper()}] GUARDRAIL BLOCK {corp_name}({ticker}){source_tag}",
        f"conf={confidence} blocked={skip_reason} shadow={shadow_tag}",
        headline[:120],
    ]
    return "\n".join(lines)


def format_sell_signal(
    *,
    ticker: str,
    exit_type: str,
    horizon: str,
    ret_pct: float,
    pnl_won: float,
    confidence: int = 0,
    size_won: float = 0.0,
    hold_seconds: int = 0,
    mode: str = "paper",
    open_positions: int | None = None,
    position_closed: bool = True,
    remaining_size_won: float = 0.0,
    exit_fraction: float = 1.0,
    cumulative_pnl_won: float = 0.0,
    cumulative_ret_pct: float = 0.0,
) -> str:
    """Format a real-time SELL/close notification."""
    lines = [
        f"{'🔴' if pnl_won < 0 else '✅'} [{mode.upper()}] SELL {ticker}",
        f"exit={exit_type} horizon={horizon} ret={ret_pct:+.2f}% pnl={pnl_won:+.0f}won",
    ]
    meta_parts: list[str] = []
    if confidence > 0:
        meta_parts.append(f"conf={confidence}")
    if size_won > 0:
        meta_parts.append(f"size={size_won:.0f}won")
    if exit_fraction < 1.0:
        meta_parts.append(f"exit_fraction={exit_fraction:.2f}")
    if hold_seconds > 0:
        meta_parts.append(f"hold={hold_seconds}s")
    if open_positions is not None:
        meta_parts.append(f"positions={open_positions}")
    meta_parts.append(f"status={'final' if position_closed else 'partial'}")
    if remaining_size_won > 0:
        meta_parts.append(f"remaining={remaining_size_won:.0f}won")
    if meta_parts:
        lines.append(" ".join(meta_parts))
    if position_closed and cumulative_pnl_won:
        lines.append(f"cumulative_ret={cumulative_ret_pct:+.2f}% cumulative_pnl={cumulative_pnl_won:+.0f}won")
    return "\n".join(lines)


def format_daily_summary_signal(
    summary: DailySummary,
    *,
    open_positions: int,
    daily_pnl_won: float,
    consecutive_stop_losses: int,
    report_path: str = "",
) -> str:
    """Format an end-of-day performance summary for Telegram."""
    lines = [
        f"📘 Kindshot Daily Summary {summary.date}",
        f"trades={summary.total_trades} wins={summary.wins} losses={summary.losses} win_rate={summary.win_rate:.1f}%",
        f"realized_pnl={summary.total_pnl_won:+.0f}won ({summary.total_pnl_pct:+.2f}%) guardrail_pnl={daily_pnl_won:+.0f}won",
        f"open_positions={open_positions} consecutive_stop_losses={consecutive_stop_losses}",
    ]
    if summary.trades:
        recent = ", ".join(f"{trade.ticker}:{trade.pnl_pct:+.2f}%" for trade in summary.trades[-3:])
        lines.append(f"recent={recent}")
    if report_path:
        lines.append(f"summary={_sanitize_path(report_path)}")
    return "\n".join(lines)


class DailySummaryNotifier:
    """Persist once-per-day Telegram summary send state."""

    def __init__(self, state_path: Path, *, close_delay_s: float) -> None:
        self._state_path = state_path
        self._close_delay_s = close_delay_s
        self._last_sent_date = ""
        self._load()

    def _load(self) -> None:
        if not self._state_path.exists():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("Daily summary notifier state load failed", exc_info=True)
            return
        self._last_sent_date = str(payload.get("last_sent_date", "") or "").strip()

    def _persist(self) -> None:
        payload = {"last_sent_date": self._last_sent_date}
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def should_send(self, now: datetime | None = None) -> bool:
        current = now.astimezone(_KST) if now is not None else datetime.now(_KST)
        today = current.strftime("%Y-%m-%d")
        if self._last_sent_date == today:
            return False
        market_close_ready = current.replace(hour=15, minute=30, second=0, microsecond=0) + timedelta(seconds=self._close_delay_s)
        return current >= market_close_ready

    def mark_sent(self, date_str: str) -> None:
        self._last_sent_date = date_str
        self._persist()


def try_send_high_conf_skip(
    *,
    ticker: str,
    corp_name: str,
    headline: str,
    confidence: int,
    skip_reason: str,
    shadow_scheduled: bool = False,
    decision_source: str = "LLM",
    mode: str = "paper",
) -> bool:
    """Best-effort high-confidence SKIP telegram notification. Never raises."""
    target = _telegram_target()
    if target is None:
        return False
    bot_token, chat_id = target
    try:
        text = format_high_conf_skip_signal(
            ticker=ticker, corp_name=corp_name, headline=headline,
            confidence=confidence, skip_reason=skip_reason,
            shadow_scheduled=shadow_scheduled,
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
    tp_pct: float | None = None,
    sl_pct: float | None = None,
) -> bool:
    """Best-effort BUY signal telegram notification. Never raises."""
    target = _telegram_target()
    if target is None:
        return False
    bot_token, chat_id = target
    try:
        text = format_buy_signal(
            ticker=ticker, corp_name=corp_name, headline=headline,
            bucket=bucket, confidence=confidence, size_hint=size_hint,
            reason=reason, keyword_hits=keyword_hits,
            hold_minutes=hold_minutes, ret_today=ret_today,
            spread_bps=spread_bps, adv_display=adv_display, mode=mode,
            decision_source=decision_source,
            tp_pct=tp_pct, sl_pct=sl_pct,
        )
        return send_telegram_message(text, bot_token, chat_id)
    except Exception:
        logger.debug("BUY signal telegram send failed", exc_info=True)
        return False


def try_send_sell_signal(
    *,
    ticker: str,
    exit_type: str,
    horizon: str,
    ret_pct: float,
    pnl_won: float,
    confidence: int = 0,
    size_won: float = 0.0,
    hold_seconds: int = 0,
    mode: str = "paper",
    open_positions: int | None = None,
    position_closed: bool = True,
    remaining_size_won: float = 0.0,
    exit_fraction: float = 1.0,
    cumulative_pnl_won: float = 0.0,
    cumulative_ret_pct: float = 0.0,
) -> bool:
    """Best-effort SELL signal telegram notification. Never raises."""
    target = _telegram_target()
    if target is None:
        return False
    bot_token, chat_id = target
    try:
        text = format_sell_signal(
            ticker=ticker,
            exit_type=exit_type,
            horizon=horizon,
            ret_pct=ret_pct,
            pnl_won=pnl_won,
            confidence=confidence,
            size_won=size_won,
            hold_seconds=hold_seconds,
            mode=mode,
            open_positions=open_positions,
            position_closed=position_closed,
            remaining_size_won=remaining_size_won,
            exit_fraction=exit_fraction,
            cumulative_pnl_won=cumulative_pnl_won,
            cumulative_ret_pct=cumulative_ret_pct,
        )
        return send_telegram_message(text, bot_token, chat_id)
    except Exception:
        logger.debug("SELL signal telegram send failed", exc_info=True)
        return False


def format_intraday_update(
    metrics: dict[str, float | int],
    *,
    open_positions: int,
    consecutive_stop_losses: int,
) -> str:
    """Format an intraday performance update for Telegram."""
    total = int(metrics.get("total_trades", 0))
    wins = int(metrics.get("wins", 0))
    losses = int(metrics.get("losses", 0))
    win_rate = float(metrics.get("win_rate", 0.0))
    total_pnl_pct = float(metrics.get("total_pnl_pct", 0.0))
    total_pnl_won = float(metrics.get("total_pnl_won", 0.0))
    avg_pnl_pct = float(metrics.get("avg_pnl_pct", 0.0))
    peak_ret_pct = float(metrics.get("peak_ret_pct", 0.0))
    mdd_pct = float(metrics.get("mdd_pct", 0.0))
    now_str = datetime.now(_KST).strftime("%H:%M")
    emoji = "\U0001f4c8" if total_pnl_won >= 0 else "\U0001f4c9"  # chart_increasing / chart_decreasing
    lines = [
        f"{emoji} [{now_str}] Kindshot Intraday",
        f"trades={total} W={wins} L={losses} win_rate={win_rate:.1f}%",
        f"pnl={total_pnl_won:+.0f}won ({total_pnl_pct:+.2f}%) avg={avg_pnl_pct:+.2f}%",
        f"peak={peak_ret_pct:+.2f}% mdd={mdd_pct:+.2f}%",
        f"positions={open_positions} streak_loss={consecutive_stop_losses}",
    ]
    return "\n".join(lines)


def try_send_intraday_update(
    metrics: dict[str, float | int],
    *,
    open_positions: int,
    consecutive_stop_losses: int,
) -> bool:
    """Best-effort intraday performance telegram. Never raises."""
    target = _telegram_target()
    if target is None:
        return False
    bot_token, chat_id = target
    try:
        text = format_intraday_update(
            metrics,
            open_positions=open_positions,
            consecutive_stop_losses=consecutive_stop_losses,
        )
        return send_telegram_message(text, bot_token, chat_id)
    except Exception:
        logger.debug("Intraday update telegram send failed", exc_info=True)
        return False


def try_send_daily_summary(
    summary: DailySummary,
    *,
    open_positions: int,
    daily_pnl_won: float,
    consecutive_stop_losses: int,
    report_path: str = "",
) -> bool:
    """Best-effort end-of-day summary telegram notification. Never raises."""
    target = _telegram_target()
    if target is None:
        return False
    bot_token, chat_id = target
    try:
        text = format_daily_summary_signal(
            summary,
            open_positions=open_positions,
            daily_pnl_won=daily_pnl_won,
            consecutive_stop_losses=consecutive_stop_losses,
            report_path=report_path,
        )
        return send_telegram_message(text, bot_token, chat_id)
    except Exception:
        logger.debug("Daily summary telegram send failed", exc_info=True)
        return False
